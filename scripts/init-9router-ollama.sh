#!/bin/bash

echo "Waiting for 9router to start..."
until curl -s http://localhost:20128 > /dev/null; do
  sleep 2
done

echo "9router is up. Checking if Ollama Local is already configured..."
# We try to add it. If it exists, the API will update it or ignore.
curl -s -X POST 'http://localhost:20128/api/providers' \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  --data-raw '{
    "provider": "ollama-local",
    "name": "Ollama Local",
    "apiKey": "Ollama",
    "priority": 1,
    "proxyPoolId": null,
    "testStatus": "active",
    "providerSpecificData": {
      "baseUrl": "http://host.docker.internal:11434"
    }
  }'

echo -e "\nOllama Local provider initialized."
