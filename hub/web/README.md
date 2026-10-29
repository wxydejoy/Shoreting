# Hub 页面前端

Hub 浏览器页共用 `dock_hub/assets/app.html` 外壳，里面动画交互较重的「岛屿」（章节目录、时长选择、口令输入等）用 React + Vite 写在本目录，构建成单文件 IIFE 挂到外壳上。

构建产物是 `../dock_hub/assets/rare.js` 和 `rare.css`（已提交，日常不需要重新构建）。改了 `src/` 才需要：

```bash
cd hub/web
npm install
npm run build
```

`vite.config.ts` 的 `outDir` 直接指向 `../dock_hub/assets`，`emptyOutDir: false`，不会碰旁边的图标和 pico.css。`node_modules` 不进 git。
