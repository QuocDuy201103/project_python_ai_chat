import streamlit as st
import os
import sqlite3
import dotenv

import streamlit as st
import dotenv
import os
from PIL import Image
from audio_recorder_streamlit import audio_recorder
import base64
from io import BytesIO
import google.generativeai as genai
import random
import anthropic

dotenv.load_dotenv()


# Initialize SQLite database connection
def init_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL)"""
    )
    conn.commit()
    conn.close()


# Function to add a new user to the database
def add_user(username, password):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)", (username, password)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        st.error("Username already exists!")
    finally:
        conn.close()


# Function to validate user login
def validate_login(username, password):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?", (username, password)
    )
    user = cursor.fetchone()
    conn.close()
    if user:
        return True
    return False


# Login page function
def login_page():
    st.title("Login")

    # Login form
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login_button = st.form_submit_button("Login")

    # If login button is pressed
    if login_button:
        if validate_login(username, password):
            st.success("Login successful!")
            st.session_state["logged_in"] = True
            st.session_state["username"] = username
            st.rerun()  # Rerun to switch to the app page
        else:
            st.error("Invalid username or password.")


# Register page function
def register_page():
    st.title("Register")

    with st.form("register_form"):
        new_username = st.text_input("New Username")
        new_password = st.text_input("New Password", type="password")
        register_button = st.form_submit_button("Register")

    if register_button:
        if new_username and new_password:
            add_user(new_username, new_password)
            st.success("Registration successful! You can now login.")
        else:
            st.error("Please fill both fields.")

anthropic_models = ["claude-3-5-sonnet-20240620"]

google_models = [
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


# Converts messages from OpenAI format to Google’s Gemini model format. Handles different types of content (text, image URLs, video files, audio files).
def messages_to_gemini(messages):
    gemini_messages = []
    prev_role = None
    for message in messages:
        if prev_role and (prev_role == message["role"]):
            gemini_message = gemini_messages[-1]
        else:
            gemini_message = {
                "role": "model" if message["role"] == "assistant" else "user",
                "parts": [],
            }

        for content in message["content"]:
            if content["type"] == "text":
                gemini_message["parts"].append(content["text"])
            elif content["type"] == "image_url":
                gemini_message["parts"].append(
                    base64_to_image(content["image_url"]["url"])
                )
            elif content["type"] == "video_file":
                gemini_message["parts"].append(genai.upload_file(content["video_file"]))
            elif content["type"] == "audio_file":
                gemini_message["parts"].append(genai.upload_file(content["audio_file"]))

        if prev_role != message["role"]:
            gemini_messages.append(gemini_message)

        prev_role = message["role"]

    return gemini_messages


# Similar to messages_to_gemini, but tailored for Anthropic's Claude models. It processes image URLs differently by converting them to base64 format.
def messages_to_anthropic(messages):
    anthropic_messages = []
    prev_role = None
    for message in messages:
        if prev_role and (prev_role == message["role"]):
            anthropic_message = anthropic_messages[-1]
        else:
            anthropic_message = {
                "role": message["role"],
                "content": [],
            }
        if message["content"][0]["type"] == "image_url":
            anthropic_message["content"].append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": message["content"][0]["image_url"]["url"]
                        .split(";")[0]
                        .split(":")[1],
                        "data": message["content"][0]["image_url"]["url"].split(",")[1],
                        # f"data:{img_type};base64,{img}"
                    },
                }
            )
        else:
            anthropic_message["content"].append(message["content"][0])

        if prev_role != message["role"]:
            anthropic_messages.append(anthropic_message)

        prev_role = message["role"]

    return anthropic_messages


# Sends queries to different language models (OpenAI, Google, Anthropic) and streams their responses.
# Depending on the model_type, the function calls the appropriate API and streams the response in chunks to display in real-time.
def stream_llm_response(model_params, model_type="google", api_key=None):
    response_message = ""
    # client = google.google(api_key=api_key)
    if model_type == "google":
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=model_params["model"],
            generation_config={
                "temperature": (
                    model_params["temperature"]
                    if "temperature" in model_params
                    else 0.3
                ),
            },
        )
        gemini_messages = messages_to_gemini(st.session_state.messages)

        for chunk in model.generate_content(
            contents=gemini_messages,
            stream=True,
        ):
            chunk_text = chunk.text or ""
            response_message += chunk_text
            yield chunk_text

    elif model_type == "anthropic":
        client = anthropic.Anthropic(api_key=api_key)
        with client.messages.stream(
            model=(
                model_params["model"]
                if "model" in model_params
                else "claude-3-5-sonnet-20240620"
            ),
            messages=messages_to_anthropic(st.session_state.messages),
            temperature=(
                model_params["temperature"] if "temperature" in model_params else 0.3
            ),
            max_tokens=4096,
        ) as stream:
            for text in stream.text_stream:
                response_message += text
                yield text

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": response_message,
                }
            ],
        }
    )


# Converts an image to a base64-encoded string to send via APIs that support image uploads.
def get_image_base64(image_raw):
    buffered = BytesIO()
    image_raw.save(buffered, format=image_raw.format)
    img_byte = buffered.getvalue()

    return base64.b64encode(img_byte).decode("utf-8")


# file_to_base64: Converts a file to base64 encoding.
def file_to_base64(file):
    with open(file, "rb") as f:

        return base64.b64encode(f.read())


# base64_to_image: Decodes a base64-encoded string back into an image.
def base64_to_image(base64_string):
    base64_string = base64_string.split(",")[1]

    return Image.open(BytesIO(base64.b64decode(base64_string)))

# Main function with login check and page navigation
def main():
    init_db()

    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False

    if st.session_state["logged_in"]:
        st.set_page_config(
            page_title="NotGPT",
            page_icon="👀",
            layout="centered",
            initial_sidebar_state="expanded",
        )
        app_page()  # Call the app function when logged in
    else:
        if "page" not in st.session_state:
            st.session_state["page"] = "login"

        if st.session_state["page"] == "login":
            login_page()
            if st.button("Go to Register"):
                st.session_state["page"] = "register"
                st.rerun()  # Force rerun to update the page immediately
        elif st.session_state["page"] == "register":
            register_page()
            if st.button("Go to Login"):
                st.session_state["page"] = "login"
                st.rerun()  # Force rerun to update the page immediately


anthropic_models = ["claude-3-5-sonnet-20240620"]

google_models = [
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


# Converts messages from OpenAI format to Google’s Gemini model format. Handles different types of content (text, image URLs, video files, audio files).
def messages_to_gemini(messages):
    gemini_messages = []
    prev_role = None
    for message in messages:
        if prev_role and (prev_role == message["role"]):
            gemini_message = gemini_messages[-1]
        else:
            gemini_message = {
                "role": "model" if message["role"] == "assistant" else "user",
                "parts": [],
            }

        for content in message["content"]:
            if content["type"] == "text":
                gemini_message["parts"].append(content["text"])
            elif content["type"] == "image_url":
                gemini_message["parts"].append(
                    base64_to_image(content["image_url"]["url"])
                )
            elif content["type"] == "video_file":
                gemini_message["parts"].append(genai.upload_file(content["video_file"]))
            elif content["type"] == "audio_file":
                gemini_message["parts"].append(genai.upload_file(content["audio_file"]))

        if prev_role != message["role"]:
            gemini_messages.append(gemini_message)

        prev_role = message["role"]

    return gemini_messages


# Similar to messages_to_gemini, but tailored for Anthropic's Claude models. It processes image URLs differently by converting them to base64 format.
def messages_to_anthropic(messages):
    anthropic_messages = []
    prev_role = None
    for message in messages:
        if prev_role and (prev_role == message["role"]):
            anthropic_message = anthropic_messages[-1]
        else:
            anthropic_message = {
                "role": message["role"],
                "content": [],
            }
        if message["content"][0]["type"] == "image_url":
            anthropic_message["content"].append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": message["content"][0]["image_url"]["url"]
                        .split(";")[0]
                        .split(":")[1],
                        "data": message["content"][0]["image_url"]["url"].split(",")[1],
                        # f"data:{img_type};base64,{img}"
                    },
                }
            )
        else:
            anthropic_message["content"].append(message["content"][0])

        if prev_role != message["role"]:
            anthropic_messages.append(anthropic_message)

        prev_role = message["role"]

    return anthropic_messages


# Sends queries to different language models (OpenAI, Google, Anthropic) and streams their responses.
# Depending on the model_type, the function calls the appropriate API and streams the response in chunks to display in real-time.
def stream_llm_response(model_params, model_type="google", api_key=None):
    response_message = ""
    # client = google.google(api_key=api_key)
    if model_type == "google":
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=model_params["model"],
            generation_config={
                "temperature": (
                    model_params["temperature"]
                    if "temperature" in model_params
                    else 0.3
                ),
            },
        )
        gemini_messages = messages_to_gemini(st.session_state.messages)

        for chunk in model.generate_content(
            contents=gemini_messages,
            stream=True,
        ):
            chunk_text = chunk.text or ""
            response_message += chunk_text
            yield chunk_text

    elif model_type == "anthropic":
        client = anthropic.Anthropic(api_key=api_key)
        with client.messages.stream(
            model=(
                model_params["model"]
                if "model" in model_params
                else "claude-3-5-sonnet-20240620"
            ),
            messages=messages_to_anthropic(st.session_state.messages),
            temperature=(
                model_params["temperature"] if "temperature" in model_params else 0.3
            ),
            max_tokens=4096,
        ) as stream:
            for text in stream.text_stream:
                response_message += text
                yield text

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": response_message,
                }
            ],
        }
    )


# Converts an image to a base64-encoded string to send via APIs that support image uploads.
def get_image_base64(image_raw):
    buffered = BytesIO()
    image_raw.save(buffered, format=image_raw.format)
    img_byte = buffered.getvalue()

    return base64.b64encode(img_byte).decode("utf-8")


# file_to_base64: Converts a file to base64 encoding.
def file_to_base64(file):
    with open(file, "rb") as f:

        return base64.b64encode(f.read())


# base64_to_image: Decodes a base64-encoded string back into an image.
def base64_to_image(base64_string):
    base64_string = base64_string.split(",")[1]

    return Image.open(BytesIO(base64.b64decode(base64_string)))


# App content (from app.py)
def app_page():

    # --- Header ---
    st.markdown(
        """
    <style>
    @keyframes rainbow {
        0% { color: red; }
        16% { color: orange; }
        32% { color: yellow; }
        48% { color: green; }
        64% { color: blue; }
        80% { color: indigo; }
        100% { color: violet; }
    }

    .rainbow-text {
        font-size: 48px;
        text-align: center;
        font-style: bold;
        animation: rainbow 8s infinite;
    }
    </style>
    <h1 class="rainbow-text">NotGPT</h1>
    """,
        unsafe_allow_html=True,
    )

    # --- Side Bar ---
    with st.sidebar:
        # Create a horizontal layout with two elements: the welcome message and logout button
        cols = st.columns([4, 1])  # Adjust the column ratios as needed

        # Welcome title
        with cols[0]:
            st.title(f"Welcome, {st.session_state['username']}!")
            if st.button("Logout"):
                # Clear the session state for logging out
                st.session_state.clear()
                st.experimental_rerun()  # Rerun the app after logout to refresh

        # # Logout button
        # with cols[1]:
        #     if st.button("Logout"):
        #         # Clear the session state for logging out
        #         st.session_state.clear()
        #         st.experimental_rerun()  # Rerun the app after logout to refresh
        
        st.divider()
        
        # Other sidebar elements
        cols_keys = st.columns(1)
        with cols_keys[0]:
            default_google_api_key = (
                os.getenv("GOOGLE_API_KEY")
                if os.getenv("GOOGLE_API_KEY") is not None
                else ""
            )  # only for development environment, otherwise it should return None
            with st.popover("Google API"):
                google_api_key = st.text_input(
                    "Introduce your Google API Key (https://aistudio.google.com/app/apikey)",
                    value=default_google_api_key,
                    type="password",
                )

        default_anthropic_api_key = (
            os.getenv("ANTHROPIC_API_KEY")
            if os.getenv("ANTHROPIC_API_KEY") is not None
            else ""
        )
        with st.popover("Anthropic API"):
            anthropic_api_key = st.text_input(
                "Introduce your Anthropic API Key (https://console.anthropic.com/)",
                value=default_anthropic_api_key,
                type="password",
            )

    # --- Main Content ---
    # Checking if the user has introduced the OpenAI API Key, if not, a warning is displayed
    if (google_api_key == "" or google_api_key is None) and (
        anthropic_api_key == "" or anthropic_api_key is None
    ):
        st.write("#")
        st.warning("⬅️ Please introduce an API Key to continue...")

        with st.sidebar:
            st.write("#")
            st.write("#")

    else:
        if "messages" not in st.session_state:
            st.session_state.messages = []

        # Displaying the previous messages if there are any
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                for content in message["content"]:
                    if content["type"] == "text":
                        st.write(content["text"])
                    elif content["type"] == "image_url":
                        st.image(content["image_url"]["url"])
                    elif content["type"] == "video_file":
                        st.video(content["video_file"])
                    elif content["type"] == "audio_file":
                        st.audio(content["audio_file"])

        # Side bar model options and inputs
        with st.sidebar:

            st.divider()

            available_models = (
                []
                + (anthropic_models if anthropic_api_key else [])
                + (google_models if google_api_key else [])
            )
            model = st.selectbox("Select a model:", available_models, index=0)
            model_type = None
            if model.startswith("gemini"):
                model_type = "google"
            elif model.startswith("claude"):
                model_type = "anthropic"

            with st.popover("Model parameters"):
                model_temp = st.slider(
                    "Temperature", min_value=0.0, max_value=2.0, value=0.3, step=0.1
                )

            model_params = {
                "model": model,
                "temperature": model_temp,
            }

            def reset_conversation():
                if (
                    "messages" in st.session_state
                    and len(st.session_state.messages) > 0
                ):
                    st.session_state.pop("messages", None)

            st.button(
                "🗑️ Reset conversation",
                on_click=reset_conversation,
            )

            st.divider()

            # Image Upload
            if model in [
                "gemini-1.5-flash",
                "gemini-1.5-pro",
                "claude-3-5-sonnet-20240620",
            ]:

                st.write(f"### **Add an image{' 'if model_type=='google' else ''}:**")

                def add_image_to_messages():
                    if st.session_state.uploaded_img or (
                        "camera_img" in st.session_state and st.session_state.camera_img
                    ):
                        img_type = (
                            st.session_state.uploaded_img.type
                            if st.session_state.uploaded_img
                            else "image/jpeg"
                        )
                        if img_type == "video/mp4":
                            # save the video file
                            video_id = random.randint(100000, 999999)
                            with open(f"video_{video_id}.mp4", "wb") as f:
                                f.write(st.session_state.uploaded_img.read())
                            st.session_state.messages.append(
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "video_file",
                                            "video_file": f"video_{video_id}.mp4",
                                        }
                                    ],
                                }
                            )
                        else:
                            raw_img = Image.open(
                                st.session_state.uploaded_img
                                or st.session_state.camera_img
                            )
                            img = get_image_base64(raw_img)
                            st.session_state.messages.append(
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:{img_type};base64,{img}"
                                            },
                                        }
                                    ],
                                }
                            )

                cols_img = st.columns(2)

                with cols_img[0]:
                    with st.popover("📁 Upload"):
                        st.file_uploader(
                            f"Upload an image{' or a video' if model_type == 'google' else ''}:",
                            type=["png", "jpg", "jpeg"]
                            + (["mp4"] if model_type == "google" else []),
                            accept_multiple_files=False,
                            key="uploaded_img",
                            on_change=add_image_to_messages,
                        )

            # Audio Upload
            st.write("#")
            st.write(
                f"### **Add an audio{' (Speech To Text)' if model_type == 'google' else ''}:**"
            )

            audio_prompt = None
            audio_file_added = False
            if "prev_speech_hash" not in st.session_state:
                st.session_state.prev_speech_hash = None

            speech_input = audio_recorder(
                "Press to talk:",
                icon_size="3x",
                neutral_color="#6ca395",
            )
            if speech_input and st.session_state.prev_speech_hash != hash(speech_input):
                st.session_state.prev_speech_hash = hash(speech_input)
                if model_type != "google":
                    transcript = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=("audio.wav", speech_input),
                    )

                    audio_prompt = transcript.text

                elif model_type == "google":
                    # save the audio file
                    audio_id = random.randint(100000, 999999)
                    with open(f"audio_{audio_id}.wav", "wb") as f:
                        f.write(speech_input)

                    st.session_state.messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "audio_file",
                                    "audio_file": f"audio_{audio_id}.wav",
                                }
                            ],
                        }
                    )

                    audio_file_added = True

            st.divider()

        # Chat input
        if (
            prompt := st.chat_input("Hi! Ask me anything...")
            or audio_prompt
            or audio_file_added
        ):
            if not audio_file_added:
                st.session_state.messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt or audio_prompt,
                            }
                        ],
                    }
                )

                # Display the new messages
                with st.chat_message("user"):
                    st.markdown(prompt)

            else:
                # Display the audio file
                with st.chat_message("user"):
                    st.audio(f"audio_{audio_id}.wav")

            with st.chat_message("assistant"):
                model2key = {
                    "google": google_api_key,
                    "anthropic": anthropic_api_key,
                }
                st.write_stream(
                    stream_llm_response(
                        model_params=model_params,
                        model_type=model_type,
                        api_key=model2key[model_type],
                    )
                )

if __name__ == "__main__":
    main()
