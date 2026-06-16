with open("services/matching_engine.py", "r") as f:
    c = f.read()

# Fix duplicate parameters
c = c.replace("""        interests: Optional[str] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        caller_age: Optional[int] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        caller_age: Optional[int] = None""", """        interests: Optional[str] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        caller_age: Optional[int] = None""")

with open("services/matching_engine.py", "w") as f:
    f.write(c)
