try:
    raise NameError
except (ValueError, KeyError):
    print("Error")
