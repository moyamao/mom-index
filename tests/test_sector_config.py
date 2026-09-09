import unittest

from keyword_config import set_sector


class SectorConfigTest(unittest.TestCase):
    def test_rejects_invalid_sector_code_before_database_access(self):
        with self.assertRaisesRegex(ValueError, '类目编码'):
            set_sector('机器人', '机器人', '#123456', True)

    def test_rejects_invalid_color_before_database_access(self):
        with self.assertRaisesRegex(ValueError, '颜色'):
            set_sector('robotics', '机器人', 'blue', True)

    def test_rejects_empty_name_before_database_access(self):
        with self.assertRaisesRegex(ValueError, '类目名称'):
            set_sector('robotics', '', '#123456', True)


if __name__ == '__main__':
    unittest.main()
