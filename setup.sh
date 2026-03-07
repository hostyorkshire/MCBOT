#!/bin/bash

# Check if .env.example exists
if [ ! -f .env.example ]; then
    echo ".env.example file is missing!"
    exit 1
fi

# Prompt for .env configuration

echo "Creating/updating .env file from .env.example..."

# Load defaults if .env exists
if [ -f .env ]; then
    echo "A .env file already exists."
    read -p "Would you like to overwrite it? (Y/n): " overwrite
    if [ "$overwrite" != "Y" ] && [ "$overwrite" != "y" ]; then
        echo "Creating a backup of .env file..."
        cp .env ".env.backup_$(date +%Y%m%d_%H%M%S)"
        echo "Backup created."
    fi
fi

# Read values from .env.example with default values
GROQ_API_KEY=$(grep 'GROQ_API_KEY' .env.example | cut -d '=' -f2- | xargs)
GROQ_MODEL=$(grep 'GROQ_MODEL' .env.example | cut -d '=' -f2- | xargs)
SERIAL_PORT=$(grep 'SERIAL_PORT' .env.example | cut -d '=' -f2- | xargs)
BAUD_RATE=$(grep 'BAUD_RATE' .env.example | cut -d '=' -f2- | xargs)
MAX_CHUNK_SIZE=$(grep 'MAX_CHUNK_SIZE' .env.example | cut -d '=' -f2- | xargs)
CHUNK_DELAY=$(grep 'CHUNK_DELAY' .env.example | cut -d '=' -f2- | xargs)
MAX_HISTORY=$(grep 'MAX_HISTORY' .env.example | cut -d '=' -f2- | xargs)

# Prompt for each variable
read -p "GROQ_API_KEY (Default: $GROQ_API_KEY): " input
GROQ_API_KEY=${input:-$GROQ_API_KEY}

# Validate GROQ_API_KEY
if [ -z "$GROQ_API_KEY" ]; then
    echo "GROQ_API_KEY cannot be empty!"
    exit 1
fi

read -p "GROQ_MODEL (Default: $GROQ_MODEL): " input
GROQ_MODEL=${input:-$GROQ_MODEL}
read -p "SERIAL_PORT (Default: $SERIAL_PORT): " input
SERIAL_PORT=${input:-$SERIAL_PORT}
read -p "BAUD_RATE (Default: $BAUD_RATE): " input
BAUD_RATE=${input:-$BAUD_RATE}
read -p "MAX_CHUNK_SIZE (Default: $MAX_CHUNK_SIZE): " input
MAX_CHUNK_SIZE=${input:-$MAX_CHUNK_SIZE}
read -p "CHUNK_DELAY (Default: $CHUNK_DELAY): " input
CHUNK_DELAY=${input:-$CHUNK_DELAY}
read -p "MAX_HISTORY (Default: $MAX_HISTORY): " input
MAX_HISTORY=${input:-$MAX_HISTORY}

# Write to .env file

echo "# .env configuration file\n# Auto-generated script: setup.sh\n# Make sure to set these values correctly" > .env

echo "GROQ_API_KEY=$GROQ_API_KEY" >> .env

echo "GROQ_MODEL=$GROQ_MODEL" >> .env

echo "SERIAL_PORT=$SERIAL_PORT" >> .env

echo "BAUD_RATE=$BAUD_RATE" >> .env

echo "MAX_CHUNK_SIZE=$MAX_CHUNK_SIZE" >> .env

echo "CHUNK_DELAY=$CHUNK_DELAY" >> .env

echo "MAX_HISTORY=$MAX_HISTORY" >> .env

# Print next steps
echo "\nNext steps:\n1. Run: pip install -r requirements.txt\n2. Run: python cyoa_bot.py"