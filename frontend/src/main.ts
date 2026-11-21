import { createApp } from 'vue';
import App from './App.vue';

const root = document.getElementById('root');
if (!root) throw Error('工作台挂载节点缺失。');
createApp(App).mount(root);
