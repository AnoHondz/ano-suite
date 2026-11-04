import unittest
from ano.reliability_defaults import apply
from ano.paid import MODEL


class Settings:
    def __init__(self):self.values={'brain':'openai','openai_model':'gpt-4.1'}
    def setting(self,key,default=None):return self.values.get(key,default)
    def set_setting(self,key,value):self.values[key]=value


class ReliabilityDefaultsTests(unittest.TestCase):
    def test_free_first_migration_preserves_previous_choice_and_later_explicit_settings(self):
        memory=Settings()
        self.assertTrue(apply(memory,['ano-chat:latest']))
        self.assertEqual(memory.values['brain'],'local')
        self.assertEqual(memory.values['openai_model'],MODEL)
        self.assertEqual(memory.values['local_chat_model'],'ano-chat:latest')
        self.assertEqual(memory.values['before_reliability_defaults_v1']['brain'],'openai')
        memory.values['brain']='openai'
        self.assertFalse(apply(memory,['ano-chat:latest']))
        self.assertEqual(memory.values['brain'],'openai')
