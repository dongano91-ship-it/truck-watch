# Railway Procfile for truck-watch
# Documentation: https://docs.railway.com/develop/prodfile

web: python watcher.py --once   # GitHub Actions compatible runner (for CI)
worker: python watcher.py       # Main polling worker (keeps alive)
