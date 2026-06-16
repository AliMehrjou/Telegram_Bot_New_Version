with open("bot/handlers/anonymous_chat.py", "r") as f:
    c = f.read()

# I used a simple replace before which might have left weird spacing.
c = c.replace("    if partner_id:\n        \n", "")

with open("bot/handlers/anonymous_chat.py", "w") as f:
    f.write(c)
