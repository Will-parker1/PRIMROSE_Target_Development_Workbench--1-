"""Legacy test entry point; use unittest discovery for the complete suite."""

if __name__ == "__main__":
    import unittest

    unittest.main(module="test_application", verbosity=2)
