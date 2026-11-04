import unittest
from ano.model_roles import select_model_roles


class Settings:
    def __init__(self, values):
        self.values = values

    def setting(self, key, default):
        return self.values.get(key, default)


class ModelRolesTests(unittest.TestCase):
    def test_arbitrary_installed_local_models_work_for_all_roles(self):
        memory = Settings(dict(local_agent_model='qwen3.5:4b', local_chat_model='qwen3.5:4b',
                               local_writer_model='qwen3.5:4b', local_vision_model='qwen3.5:4b'))
        roles = select_model_roles(memory, 'old:latest', ['old:latest', 'qwen3.5:4b'])
        self.assertEqual((roles.agent, roles.conversation, roles.writer, roles.vision), ('qwen3.5:4b',) * 4)
        self.assertEqual(roles.warnings, ())

    def test_missing_and_remote_overrides_fall_back_without_mutation(self):
        values = dict(local_agent_model='missing:latest', local_chat_model='remote/cloud',
                      local_writer_model='https://example.org', local_vision_model='vision@remote')
        original = values.copy()
        roles = select_model_roles(Settings(values), 'base:latest', ['base:latest'])
        self.assertEqual((roles.agent, roles.conversation, roles.writer, roles.vision), ('base:latest',) * 4)
        self.assertEqual(len(roles.warnings), 4)
        self.assertEqual(values, original)

    def test_defaults_follow_selected_agent_without_alias_rewriting(self):
        roles = select_model_roles(Settings({'local_agent_model': 'quality:latest'}),
                                   'vision:latest', ['quality:latest', 'vision:latest'])
        self.assertEqual((roles.agent, roles.conversation, roles.writer), ('quality:latest',) * 3)
        self.assertEqual(roles.vision, 'vision:latest')
        with self.assertRaises(ValueError):
            select_model_roles(Settings({}), 'quality:cloud', [])
