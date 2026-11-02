import os
import unittest
from unittest import mock
from takumi.oauth import initiate_sign_in

class OAuthTests(unittest.TestCase):
    def test_allowlist(self):
        with self.assertRaises(ValueError):
            initiate_sign_in("unsupported")
    def test_windows_only(self):
        if os.name == "nt":
            self.skipTest("Windows sign-in launches a real authentication window")
        with self.assertRaises(RuntimeError):
            initiate_sign_in("github")
        with self.assertRaises(RuntimeError):
            initiate_sign_in("figma")

if __name__=='__main__': unittest.main()
