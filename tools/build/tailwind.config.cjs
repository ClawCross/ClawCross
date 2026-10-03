const path = require('node:path');
module.exports = {
  content: [
    path.join(__dirname, '../../src/frontend/templates/**/*.html'),
    path.join(__dirname, '../../src/frontend/static/js/*.js'),
    '!' + path.join(__dirname, '../../src/frontend/static/js/oasis-town.bundle.js'),
  ],
  theme: { extend: {} },
  plugins: [],
};
