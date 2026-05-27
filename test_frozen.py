import sys, os
print("MEIPASS:", getattr(sys, "_MEIPASS", "NOT SET"))
print("FROZEN:", getattr(sys, "frozen", "NOT SET"))
print("DIRNAME:", os.path.dirname(os.path.abspath(__file__)))
