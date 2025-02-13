from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import datetime
import openai
import requests
import os
import pickle
import dateparser
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from config import OPENAI_API_KEY, WEATHER_API_KEY

app = Flask(__name__)
app.secret_key = 'your-secret-key'

# --- OpenAI & Conversation Memory ---
openai.api_key = OPENAI_API_KEY
conversation_history = []  # Global conversation history for AI chat

def get_ai_response(prompt):
    conversation_history.append({"role": "user", "content": prompt})
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",  # Change to "gpt-3.5-turbo" if needed
            messages=conversation_history
        )
        ai_response = response.choices[0].message.content.strip()
        conversation_history.append({"role": "assistant", "content": ai_response})
        return ai_response
    except Exception as e:
        print(f"Error with OpenAI: {e}")
        return "There was an error with the AI service."

def get_weather(location):
    url = f"https://api.openweathermap.org/data/2.5/weather?q={location}&appid={WEATHER_API_KEY}&units=metric"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        weather = data['weather'][0]['description']
        temp = data['main']['temp']
        return f"The current weather in {location} is {weather} with a temperature of {temp}°C."
    elif response.status_code == 401:
        return "Invalid API key. Please check your OpenWeather API key."
    elif response.status_code == 404:
        return "City not found. Please check the location name."
    else:
        return "I couldn't fetch the weather. Please try again later."

# --- Google Calendar Setup & Functions ---
SCOPES = ["https://www.googleapis.com/auth/calendar"]

def authenticate_google_calendar():
    creds = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)
    return build("calendar", "v3", credentials=creds)

def add_event_to_calendar(task, event_time):
    service = authenticate_google_calendar()
    event = {
        "summary": task,
        "start": {"dateTime": event_time.isoformat(), "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": (event_time + datetime.timedelta(hours=1)).isoformat(), "timeZone": "America/Los_Angeles"},
        "reminders": {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": 10},
            {"method": "email", "minutes": 30}
        ]}
    }
    created_event = service.events().insert(calendarId="primary", body=event).execute()
    return f"Task '{task}' added to Google Calendar at {event_time}!"

def find_calendar_event_by_time(target_time, tolerance_minutes=30):
    service = authenticate_google_calendar()
    time_min = (target_time - datetime.timedelta(minutes=tolerance_minutes)).isoformat() + "Z"
    time_max = (target_time + datetime.timedelta(minutes=tolerance_minutes)).isoformat() + "Z"
    events_result = service.events().list(calendarId="primary", timeMin=time_min, timeMax=time_max,
                                          singleEvents=True, orderBy="startTime").execute()
    events = events_result.get('items', [])
    return events

def update_event_in_calendar(event_id, new_start_time):
    service = authenticate_google_calendar()
    event = service.events().get(calendarId="primary", eventId=event_id).execute()
    event['start']['dateTime'] = new_start_time.isoformat()
    event['end']['dateTime'] = (new_start_time + datetime.timedelta(hours=1)).isoformat()
    updated_event = service.events().update(calendarId="primary", eventId=event_id, body=event).execute()
    return updated_event

def delete_event_from_calendar(event_id):
    service = authenticate_google_calendar()
    service.events().delete(calendarId="primary", eventId=event_id).execute()
    return True

# --- Process Voice Command ---
@app.route('/process_voice', methods=['POST'])
def process_voice():
    data = request.get_json()
    command = data.get('command', '').strip()
    now = datetime.datetime.now()
    response_text = ""
    chat_type = "chat"  # default type

    # Check for any pending action (confirmation for edit/delete)
    pending_action = session.get('pending_action')
    if pending_action:
        if command.lower() in ["yes", "yeah", "yep"]:
            if pending_action['action'] == 'edit':
                new_time = dateparser.parse(pending_action['new_time'])
                update_event_in_calendar(pending_action['event_id'], new_time)
                response_text = "Event updated successfully."
            elif pending_action['action'] == 'delete':
                delete_event_from_calendar(pending_action['event_id'])
                response_text = "Event deleted successfully."
            session.pop('pending_action')
            history = session.get('history', [])
            history.append({'command': command, 'response': response_text, 'type': pending_action['action']})
            session['history'] = history
            return jsonify({'response': response_text})
        elif command.lower() in ["no", "nope"]:
            session.pop('pending_action')
            response_text = "Action cancelled. Please provide further instructions if needed."
            history = session.get('history', [])
            history.append({'command': command, 'response': response_text, 'type': pending_action['action']})
            session['history'] = history
            return jsonify({'response': response_text})
        else:
            # If the response is not clearly yes or no, re-prompt confirmation
            response_text = pending_action.get('confirmation_text', "Please confirm your action with yes or no.")
            return jsonify({'response': response_text})

    # Process new commands:
    # Weather command
    if "weather" in command.lower():
        location = command.lower().replace("weather in", "").strip()
        response_text = get_weather(location)
    # Calendar event creation
    elif "add event" in command.lower() or "remind me" in command.lower():
        chat_type = "calendar"
        parts = command.lower().split(" at ")
        if len(parts) >= 2:
            task = parts[0].replace("remind me to", "").replace("add event", "").strip()
            time_expression = " at ".join(parts[1:]).strip()
            event_time = dateparser.parse(time_expression)
            if event_time is None:
                event_time = now.replace(second=0, microsecond=0)
        else:
            task = command.lower().replace("remind me to", "").replace("add event", "").strip()
            event_time = now.replace(second=0, microsecond=0)
        try:
            response_text = add_event_to_calendar(task, event_time)
        except Exception as e:
            print(f"Calendar error: {e}")
            response_text = "There was an error adding the event."
    # Edit schedule command
    elif any(keyword in command.lower() for keyword in ["edit", "change", "reschedule"]):
        chat_type = "calendar"
        # Expected format: "change my schedule from [old time] to [new time]"
        parts = command.lower().split(" from ")
        if len(parts) >= 2:
            task = parts[0].replace("edit", "").replace("change", "").replace("reschedule", "").strip()
            times = parts[1].split(" to ")
            if len(times) == 2:
                old_time_expr = times[0].strip()
                new_time_expr = times[1].strip()
                old_time = dateparser.parse(old_time_expr)
                new_time = dateparser.parse(new_time_expr)
                if old_time and new_time:
                    events = find_calendar_event_by_time(old_time)
                    if len(events) == 0:
                        response_text = "No matching event found near that time."
                    elif len(events) == 1:
                        event = events[0]
                        confirmation = (f"Do you want to update your event '{event.get('summary', 'No Title')}' "
                                        f"from {old_time.strftime('%I:%M %p')} to {new_time.strftime('%I:%M %p')}?")
                        session['pending_action'] = {
                            'action': 'edit',
                            'event_id': event['id'],
                            'new_time': new_time_expr,
                            'confirmation_text': confirmation
                        }
                        response_text = confirmation
                    else:
                        response_text = "Multiple events found. Please specify which one you want to edit."
                else:
                    response_text = "Could not parse the times. Please try again."
            else:
                response_text = "Please use the format 'from [old time] to [new time]'."
        else:
            response_text = "Please specify the time range for editing."
    # Delete schedule command
    elif any(keyword in command.lower() for keyword in ["delete", "remove", "cancel", "get rid of"]):
        chat_type = "calendar"
        # Attempt to parse a time from the command
        target_time = dateparser.parse(command)
        if target_time:
            events = find_calendar_event_by_time(target_time)
            if len(events) == 0:
                response_text = "No matching event found near that time."
            elif len(events) == 1:
                event = events[0]
                confirmation = (f"Do you want to delete your event '{event.get('summary', 'No Title')}' "
                                f"scheduled at {target_time.strftime('%I:%M %p')}?")
                session['pending_action'] = {
                    'action': 'delete',
                    'event_id': event['id'],
                    'confirmation_text': confirmation
                }
                response_text = confirmation
            else:
                response_text = "Multiple events found. Please specify which one you want to delete."
        else:
            response_text = "Couldn't parse the target time for deletion."
    # General AI chat command
    else:
        response_text = get_ai_response(command)
        chat_type = "chat"

    history = session.get('history', [])
    history.append({'command': command, 'response': response_text, 'type': chat_type})
    session['history'] = history

    return jsonify({'response': response_text})

# --- Dummy User Store (for demo purposes) ---
users = {}

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/history')
def history():
    conv_history = session.get('history', [])
    filter_type = request.args.get('filter', 'all')
    if filter_type != 'all':
        conv_history = [item for item in conv_history if item.get('type', 'chat') == filter_type]
    return render_template('history.html', history=conv_history, filter_type=filter_type)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username in users and users[username] == password:
            session['username'] = username
            return redirect(url_for('home'))
        else:
            error = 'Invalid credentials. Please try again.'
    return render_template('login.html', error=error)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username in users:
            error = 'Username already exists.'
        else:
            users[username] = password
            session['username'] = username
            return redirect(url_for('home'))
    return render_template('signup.html', error=error)

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)
