// App entry point for the aiHair testbed

import { initTheme, cycleTheme } from './utils/theme.js';
import { createAiHairScreen } from './components/ai-hair-screen.js';

const mainEl   = document.getElementById('main');
const themeBtn = document.getElementById('theme-btn');

// Initialize theme
initTheme(themeBtn);
themeBtn.addEventListener('click', cycleTheme);

// Mount the screen
createAiHairScreen(mainEl);
