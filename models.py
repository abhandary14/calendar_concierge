from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

# Handles pure data retrieval only: fetch emails, fetch calendar events.
# No generation — speed and reliable function-calling are all that matter here.
tool_model = ChatGroq(model="llama-3.1-8b-instant", temperature=0)

# Handles everything generative: email classification, reply drafting,
# save_draft tool calls, calendar flagging, and final structured briefing.
# Gets the full email + calendar context before making any decisions.
summary_model = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.3)
