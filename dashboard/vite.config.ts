import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dashboard gọi backend qua proxy cùng origin => không cần CORS cho /api và /ws.
// Đổi địa chỉ backend: đặt biến môi trường HRC_API (mặc định http://127.0.0.1:8000).
const api = process.env.HRC_API ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": { target: api, changeOrigin: true },
  "/ws": { target: api.replace(/^http/, "ws"), ws: true },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy },
  preview: { port: 4173, strictPort: true, proxy },
});
