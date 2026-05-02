# GRC Audit Swarm — React Frontend

This directory contains the modern **React + Vite** frontend for the GRC Audit Swarm platform. It replaces the legacy Streamlit interface to provide a more dynamic, scalable, and customizable user experience for interacting with the AI agent crews and viewing the immutable evidence vault.

## Tech Stack
- **Framework:** React 18
- **Build Tool:** Vite
- **Language:** TypeScript
- **Backend Communication:** REST API (FastAPI backend in `src/api`)

## Getting Started

### Prerequisites
Make sure you have Node.js (v18+) and npm installed. The FastAPI backend must also be running for the frontend to function properly.

### Installation
```bash
cd frontend
npm install
```

### Development Server
To start the Vite development server with Hot Module Replacement (HMR):
```bash
npm run dev
```
The application will be available at `http://localhost:5173`.

### Docker Deployment
For production or full-stack testing, the frontend is built and served via Nginx in the root `docker-compose.yml`:
```bash
cd ..
docker-compose up --build
```
This maps the frontend to port `3000`.

## Integration with FastAPI
The frontend communicates directly with the FastAPI backend defined in `src/api/`. By default, Vite proxies `/api` requests to `http://localhost:8000` during development to avoid CORS issues.

## Environment Variables
Authentication tokens and API URLs can be configured using `.env` files in this directory:
- `VITE_API_AUTH_TOKEN`: Shared bearer token used to call the protected FastAPI routes.
