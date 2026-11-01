// 构建后给 index.html 里的 js/css 地址打上版本号。
//
// 控制台的 assets/index.html 是手写的（vite 只产出 console.js / console.css），
// 里面的地址一直是固定名。手机「添加到主屏幕」的 WebView、微信内置浏览器、
// 甚至链路上的代理，都可能把 /assets/console.js 留在本地，于是「改了、也刷新
// 了，看到的还是老页面」。地址后面挂上构建时间，缓存自然失效。
//
// 由 npm run build 自动调用，不用手动跑。
import { readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const assets = path.resolve(here, "../../dock_console/assets");
const names = ["console.js", "console.css"];

const stamp = Math.round(
  Math.max(...names.map((name) => statSync(path.join(assets, name)).mtimeMs)) / 1000,
);

const index = path.join(assets, "index.html");
let html = readFileSync(index, "utf8");
for (const name of names) {
  const pattern = new RegExp(`/assets/${name.replace(/\./g, "\\.")}(\\?v=\\d+)?`, "g");
  html = html.replace(pattern, `/assets/${name}?v=${stamp}`);
}
writeFileSync(index, html);
console.log(`index.html: js/css -> v=${stamp}`);
