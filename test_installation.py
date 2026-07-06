#!/usr/bin/env python3
"""
Quick installation test script.
Verifies that all components are working correctly.
"""

import sys
import os

def test_imports():
    """Test that all required modules can be imported"""
    print("Testing imports...")
    errors = []

    try:
        import flask
        print("  ✓ Flask")
    except ImportError as e:
        errors.append(f"Flask: {e}")

    try:
        import sqlalchemy
        print("  ✓ SQLAlchemy")
    except ImportError as e:
        errors.append(f"SQLAlchemy: {e}")

    try:
        import feedparser
        print("  ✓ feedparser")
    except ImportError as e:
        errors.append(f"feedparser: {e}")

    try:
        import feedgen
        print("  ✓ feedgen")
    except ImportError as e:
        errors.append(f"feedgen: {e}")

    try:
        import requests
        print("  ✓ requests")
    except ImportError as e:
        errors.append(f"requests: {e}")

    try:
        from pydub import AudioSegment
        print("  ✓ pydub")
    except ImportError as e:
        errors.append(f"pydub: {e}")

    try:
        import numpy
        print("  ✓ numpy")
    except ImportError as e:
        errors.append(f"numpy: {e}")

    try:
        import yaml
        print("  ✓ PyYAML")
    except ImportError as e:
        errors.append(f"PyYAML: {e}")

    return errors


def test_database():
    """Test database initialization"""
    print("\nTesting database...")

    try:
        from app.database import init_db, get_session
        init_db()
        session = get_session()
        print("  ✓ Database initialized")

        # Test models
        from app.models import Podcast, Episode, RemovedSegment
        print("  ✓ Models imported")

        return []
    except Exception as e:
        return [f"Database: {e}"]


def test_config():
    """Test configuration loading"""
    print("\nTesting configuration...")

    try:
        import yaml
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)

        required_keys = ['storage', 'web', 'worker', 'audio']
        for key in required_keys:
            if key not in config:
                return [f"Config missing key: {key}"]

        print("  ✓ Configuration valid")
        return []
    except Exception as e:
        return [f"Config: {e}"]


def test_directories():
    """Test that required directories exist"""
    print("\nTesting directories...")

    errors = []
    required_dirs = [
        'data',
        'data/original',
        'data/processed',
        'data/segments'
    ]

    for directory in required_dirs:
        if not os.path.exists(directory):
            errors.append(f"Missing directory: {directory}")
        else:
            print(f"  ✓ {directory}")

    return errors


def test_app_creation():
    """Test Flask app creation"""
    print("\nTesting Flask app...")

    try:
        from app import create_app
        app = create_app()
        print("  ✓ Flask app created")

        # Test that routes are registered
        if not app.blueprints:
            return ["No blueprints registered"]

        print("  ✓ Routes registered")
        return []
    except Exception as e:
        return [f"Flask app: {e}"]


def test_system_dependencies():
    """Test system dependencies"""
    print("\nTesting system dependencies...")

    import subprocess
    errors = []

    # Test for fpcalc (chromaprint)
    try:
        subprocess.run(['fpcalc', '-version'], capture_output=True, check=True)
        print("  ✓ chromaprint (fpcalc)")
    except (subprocess.CalledProcessError, FileNotFoundError):
        errors.append("chromaprint not found (brew install chromaprint)")

    # Test for ffmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        print("  ✓ ffmpeg")
    except (subprocess.CalledProcessError, FileNotFoundError):
        errors.append("ffmpeg not found (brew install ffmpeg)")

    # Test for whisper.cpp + model (transcription for ad detection)
    try:
        subprocess.run(['whisper-cli', '--help'], capture_output=True, check=True)
        print("  ✓ whisper-cpp (whisper-cli)")
    except (subprocess.CalledProcessError, FileNotFoundError):
        errors.append("whisper-cpp not found (brew install whisper-cpp)")

    import os
    model = 'data/models/ggml-small.en.bin'
    if os.path.exists(model) and os.path.getsize(model) > 0:
        print("  ✓ whisper model")
    else:
        errors.append("whisper model missing (see setup.sh or README)")

    return errors


def main():
    """Run all tests"""
    print("=" * 50)
    print("RSS Podcast Processor - Installation Test")
    print("=" * 50)
    print()

    all_errors = []

    # Run tests
    all_errors.extend(test_system_dependencies())
    all_errors.extend(test_imports())
    all_errors.extend(test_config())
    all_errors.extend(test_directories())
    all_errors.extend(test_database())
    all_errors.extend(test_app_creation())

    # Print results
    print("\n" + "=" * 50)
    if all_errors:
        print("❌ TESTS FAILED")
        print("=" * 50)
        print("\nErrors found:")
        for error in all_errors:
            print(f"  ✗ {error}")
        print("\nPlease run ./setup.sh to fix these issues.")
        sys.exit(1)
    else:
        print("✅ ALL TESTS PASSED")
        print("=" * 50)
        print("\nInstallation is complete and working!")
        print("\nNext steps:")
        print("  1. source venv/bin/activate")
        print("  2. python run_web.py")
        print("  3. Visit http://localhost:5002")
        sys.exit(0)


if __name__ == '__main__':
    main()
