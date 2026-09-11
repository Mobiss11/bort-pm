// PM2-конфигурация «Борта». Запуск: pm2 start ecosystem.config.js
// Автостарт при загрузке: pm2 save && pm2 startup (один раз)
const path = require("path");
const home = require("os").homedir();
const root = path.join(home, "bort");

module.exports = {
  apps: [
    {
      name: "bort-web",
      cwd: root,
      script: "uv",
      args: "run uvicorn bort.web.app:app --host 127.0.0.1 --port 8100",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      env: {
        BORT_DB: path.join(root, "data", "bort.db"),
        BORT_TZ: "Europe/Moscow",
        BORT_HOST: "127.0.0.1",
        BORT_PORT: "8100",
      },
      out_file: path.join(root, "logs", "bort-web.out.log"),
      error_file: path.join(root, "logs", "bort-web.err.log"),
      merge_logs: true,
      time: true,
    },
  ],
};
