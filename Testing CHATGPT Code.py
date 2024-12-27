import pandas as pd

# Load your rates data
rate_sheet = pd.read_excel(r"C:\path\to\Test Rates.xlsx")

# Simple chatbot function
def chatbot(query):
    if "rate" in query.lower():
        license_type = query.split()[-1]  # Extract license type from query
        rate_info = rate_sheet[rate_sheet["License"] == license_type]
        if not rate_info.empty:
            return f"The rate for {license_type} is {rate_info['Rate'].values[0]}."
        else:
            return "I couldn't find the rate for that license type."
    else:
        return "I'm not sure how to answer that. Can you rephrase?"

# Test the chatbot
while True:
    user_input = input("You: ")
    if user_input.lower() == "exit":
        break
    response = chatbot(user_input)
    print(f"Bot: {response}")
