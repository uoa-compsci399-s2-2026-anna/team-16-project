# Food Waste Impact Calculator prototype

A responsive front-end prototype for Kai Commitment's proposed New Zealand Food Waste Impact Calculator. It demonstrates the guided calculator journey, form validation, multiple waste records, review, placeholder results and methodology content.

This prototype does not contain real scientific calculations, a database, authentication or data collection. Only user-entered kilograms and tonnes are converted and totalled.

## Requirements

- Node.js 20 or newer
- npm

## Run locally

```bash
npm install
npm run dev
```

Open the local URL printed by Vite, normally `http://localhost:5173`.

## Quality checks

```bash
npm run lint
npm run build
```

To preview the production build:

```bash
npm run preview
```

## Demonstration values

The non-scientific result strings and their warning text are isolated in `src/data/mockResults.js`. No CO2e, financial or water formulas or factors are included. The only conversion in the application is 1 tonne = 1,000 kilograms, used to create the user-entry total.
