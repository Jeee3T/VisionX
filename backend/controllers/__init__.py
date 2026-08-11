"""Controllers orchestrate work that spans several services.

Route modules stay thin (parse -> delegate -> envelope); anything that has to
coordinate the session store, the gesture preferences and the CV engine in one
transaction-like flow lives here.
"""
