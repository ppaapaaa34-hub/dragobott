Drago Tap Empire

The archive's Express server is the sole HTTP/API server.  The Python Telegram
bot runs separately as a Render Background Worker, so neither process binds the
other's port.

Local start

npm install
npm start

Open http://localhost:3000/healthz.  Without MONGO_URI the API uses an
in-memory fallback for development; configure MongoDB in Render for persistent
players and leaderboard data.

Render

Deploy from this repository using render.yaml, then set MONGO_URI,
TELEGRAM_TOKEN, and WEB_APP_URL (the public URL of the web service).
Point BotFather's Mini App URL to that same WEB_APP_URL.

The API routes are: POST /api/user/sync, POST /api/user/save, GET /api/leaderboard, and GET /api/admin/users/:id.
