#!/bin/bash
# Test Runner Script for Recorder File Attachments
# 
# This script runs all tests for the file attachment feature.
# Usage: bash run_attachment_tests.sh [options]
#
# Options:
#   --django      Run only Django backend tests
#   --javascript  Run only JavaScript tests
#   --all         Run all tests (default)
#   --coverage    Include coverage report
#   --verbose     Verbose output

set -e

cd "$(dirname "$0")"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default options
RUN_DJANGO=false
RUN_JS=false
RUN_COVERAGE=false
VERBOSE=""

# Parse arguments
if [ $# -eq 0 ]; then
    RUN_DJANGO=true
    RUN_JS=true
else
    for arg in "$@"; do
        case $arg in
            --django)
                RUN_DJANGO=true
                ;;
            --javascript)
                RUN_JS=true
                ;;
            --all)
                RUN_DJANGO=true
                RUN_JS=true
                ;;
            --coverage)
                RUN_COVERAGE=true
                ;;
            --verbose|-v)
                VERBOSE="-v"
                ;;
        esac
    done
fi

echo -e "${BLUE}=== Recorder File Attachments Test Suite ===${NC}\n"

# Run Django Tests
if [ "$RUN_DJANGO" = true ]; then
    echo -e "${YELLOW}Running Django Backend Tests...${NC}"
    
    if [ "$RUN_COVERAGE" = true ]; then
        echo "Including coverage report..."
        python manage.py test src.recordings.test_file_attachments \
            --verbosity=2 \
            --keepdb \
            --debug-mode
        
        # If coverage.py is installed, run coverage
        if command -v coverage &> /dev/null; then
            echo -e "\n${YELLOW}Generating Coverage Report...${NC}"
            coverage run --source='src.recordings' manage.py test src.recordings.test_file_attachments
            coverage report -m
            coverage html
            echo "Coverage report generated: htmlcov/index.html"
        fi
    else
        python manage.py test src.recordings.test_file_attachments \
            --verbosity=2 \
            --keepdb \
            $VERBOSE
    fi
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Django tests passed${NC}\n"
    else
        echo -e "${RED}✗ Django tests failed${NC}\n"
        exit 1
    fi
fi

# Run JavaScript Tests
if [ "$RUN_JS" = true ]; then
    echo -e "${YELLOW}Running JavaScript Tests...${NC}"
    
    # Check if jest is installed
    if command -v jest &> /dev/null; then
        if [ "$RUN_COVERAGE" = true ]; then
            jest src/static/recordings/js/test_audio_recorder_attachments.js \
                --coverage \
                --verbose
        else
            jest src/static/recordings/js/test_audio_recorder_attachments.js \
                --verbose
        fi
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ JavaScript tests passed${NC}\n"
        else
            echo -e "${RED}✗ JavaScript tests failed${NC}\n"
            exit 1
        fi
    elif command -v npm &> /dev/null; then
        echo "Running tests via npm..."
        npm test -- src/static/recordings/js/test_audio_recorder_attachments.js
    else
        echo -e "${YELLOW}⚠ Jest not found. Skipping JavaScript tests.${NC}"
        echo "Install with: npm install --save-dev jest"
    fi
fi

echo -e "${GREEN}=== All Tests Completed Successfully ===${NC}\n"
echo "Test files:"
echo "  - Backend: src/recordings/test_file_attachments.py"
echo "  - Frontend: src/static/recordings/js/test_audio_recorder_attachments.js"
