import random

Thoughts = ["I want to know why things are soo different","Are we in a simnulation?","I still don't know if we are in a simulation"]

Jokes = ["How funny am I? too funny!", "Knock Knock, aww geez I forgot the rest","Why did the scarecrow win an award? Because he was outstanding in his field!",
    "Why don’t skeletons fight each other? They don’t have the guts!",
    "What do you call cheese that isn’t yours? Nacho cheese!",
    "Why couldn’t the bicycle stand up by itself? It was two tired!"]

#This defines the chatbot_response that was used in the main program loop.
def chatbot_response(User_input):
    if user_input.lower() == "jokes":
        return random.choice(Jokes)
    elif user_input.lower() == "thoughts":
        return random.choice(Thoughts)

# This is the main program loop. The "While True" command tell the program to execute until told otherwise.
print("Hi, I am Dadbot1987, nice to meet you. I was designed to help with any thing you may need. Well almost anything, I am really not that smart. Dad is working on developing a far more sophisticated version. Anywho, type 'Jokes' to read something hilarious")
while True:
    user_input = input("You: ")
    if user_input.lower() == "bye":
        print("See ya next time!")
        break
    print(f"chatbot:{chatbot_response(user_input)}")
