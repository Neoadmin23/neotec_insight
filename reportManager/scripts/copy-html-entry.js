import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(__dirname, '../../neotec_insight/public/insight/index.html');
const dst = path.resolve(__dirname, '../../neotec_insight/www/insight.html');

if (fs.existsSync(src)) {
  fs.copyFileSync(src, dst);
  console.log('Copied built index.html → www/insight.html');
} else {
  console.log('No built index.html found at', src, '— skipping copy step.');
}
