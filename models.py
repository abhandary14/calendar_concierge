from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

# Handles everything generative: email classification, reply drafting,
# save_draft tool calls, calendar flagging, and final structured briefing.
summary_model = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.3)
