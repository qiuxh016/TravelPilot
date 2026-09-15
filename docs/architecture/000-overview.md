# Architecture overview

TravelPilot starts as a modular monolith with a separate worker process. The core package owns domain rules and ports; infrastructure owns PostgreSQL, Redis and provider adapters. API and worker are composition roots.

