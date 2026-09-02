import { render } from 'preact';
import { registerSW } from 'virtual:pwa-register';
import { App } from './app';
import { applyTheme, theme } from './theme';
import './styles.css';

applyTheme(theme.value);
registerSW({ immediate: true });

const root = document.getElementById('app');
if (root) render(<App />, root);
