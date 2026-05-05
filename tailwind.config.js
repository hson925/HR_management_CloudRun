/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/templates/**/*.html',
    './app/static/js/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        dybred:       '#B01116',
        sidebarbg:    '#2d3748',
        sidebarborder:'#1a202c',
      }
    }
  },
  plugins: [],
}
