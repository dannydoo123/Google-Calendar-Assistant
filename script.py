import datetime
import openai
import schedule
import time
import speech_recognition as sr
import requests
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
import os
from config import OPENAI_API_KEY, WEATHER_API_KEY

# --- OpenAI API Setup ---
openai.api_key = OPENAI_API_KEY

# --- Conversation Memory ---
conversation_history = []

# --- Weather API Setup ---
def get_ai_response(prompt):
    """Use OpenAI API to generate responses while maintaining conversation history."""
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    
    conversation_history.append({"role": "user", "content": prompt})
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=conversation_history
        )
        ai_response = response.choices[0].message.content.strip()
        conversation_history.append({"role": "assistant", "content": ai_response})
        return ai_response
    except openai.OpenAIError as e:
        print(f"⚠️ OpenAI Error: {e}")
        return "There was an error with the AI service."


def get_weather(location):
    """Fetch real-time weather information."""
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


# --- Google Calendar Setup ---
SCOPES = ["https://www.googleapis.com/auth/calendar"]

def authenticate_google_calendar():
    """Authenticate with Google Calendar API and return service object."""
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

def add_event_to_calendar(service, task, event_time):
    """Add a new task/event to Google Calendar."""
    event = {
        "summary": task,
        "start": {"dateTime": event_time.isoformat(), "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": (event_time + datetime.timedelta(hours=1)).isoformat(), "timeZone": "America/Los_Angeles"},
        "reminders": {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": 10},
            {"method": "email", "minutes": 30}
        ]}
    }
    service.events().insert(calendarId="primary", body=event).execute()
    print(f"Task '{task}' added to Google Calendar at {event_time}!")


def recognize_speech():
    """Capture voice input and convert to text only when speech is detected."""
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Waiting for speech...")
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)
    try:
        text = recognizer.recognize_google(audio)
        print(f"You said: {text}")
        return text
    except sr.UnknownValueError:
        return None
    except sr.RequestError:
        print("Could not request results.")
        return None


def process_voice_command(command, service):
    """Process voice command and take appropriate action."""
    now = datetime.datetime.now()
    if "weather" in command.lower():
        location = command.replace("weather in", "").strip()
        weather_info = get_weather(location)
        print(f"Weather Update: {weather_info}")
    elif "add event" in command.lower() or "remind me" in command.lower():
        parts = command.lower().replace("add event", "").replace("remind me to", "").strip().split(" in ")
        task = parts[0]
        if len(parts) == 2 and "minute" in parts[1]:
            try:
                minutes = int(parts[1].split()[0])
                event_time = now + datetime.timedelta(minutes=minutes)
            except ValueError:
                print("Invalid time format. Please try again.")
                return
        else:
            event_time = now.replace(hour=now.hour, minute=0, second=0, microsecond=0)
        add_event_to_calendar(service, task, event_time)
    else:
        response = get_ai_response(command)
        print(f"AI Response: {response}")


# --- Main Program ---
if __name__ == "__main__":
    service = authenticate_google_calendar()
    while True:
        command = recognize_speech()
        if command:
            process_voice_command(command, service)
