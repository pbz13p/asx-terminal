"""Round 64: round 63 (asymmetric returns) on ALL ASX stocks that trade at least $50k a day (price >= 5c),
not only the top 200. Size groups: top 50 / 51-200 / beyond 200 by value traded."""
import os

os.environ.setdefault("S63_N", "5000")
os.environ.setdefault("S63_MINVAL", "50000")
os.environ.setdefault("S63_PRICE", "0.05")
os.environ.setdefault("STUDY63_OUT", os.environ.get("STUDY64_OUT", "data/study64_results.json"))
import study63

if __name__ == "__main__":
    study63.main()
