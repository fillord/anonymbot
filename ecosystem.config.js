module.exports = {
  apps: [{
    name: "anon_bot",
    script: "./venv/bin/python",
    args: "run.py",
    cwd: "/home/yola/projects/tg_bots/yola_anon_bot",
    interpreter: "",
    autorestart: true,
    watch: false,
    env: {
      BOT_TOKEN: "8168506873:AAHWkcTTL3s5CYHYqk9OJuw0Jcn_N-RO6uo",
      DATABASE_URL: "postgresql+asyncpg://yola:23862369789@localhost/anon_chat"
    }
  }]
}