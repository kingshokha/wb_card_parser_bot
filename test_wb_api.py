import unittest

from wb_api import build_description_with_restricted_charcs, map_characteristics


class RestrictedCharacteristicsTests(unittest.TestCase):
    def setUp(self):
        self.category_charcs = [
            {
                "charcID": 90640,
                "name": "Максимальный уровень звука/шума",
                "charcType": 4,
                "maxCount": 0,
            },
            {
                "charcID": 91044,
                "name": "Скорость воздушного потока",
                "charcType": 4,
                "maxCount": 0,
            },
            {
                "charcID": 100,
                "name": "Мощность",
                "charcType": 4,
                "maxCount": 1,
            },
        ]

    def test_max_count_zero_values_are_preserved_but_not_sent_as_charcs(self):
        donor_options = [
            {"name": "Максимальный уровень звука/шума", "value": "60 дБ"},
            {"name": "Скорость воздушного потока", "value": "150 м/с"},
            {"name": "Мощность", "value": "800 Вт"},
        ]

        mapped, _, restricted = map_characteristics(donor_options, self.category_charcs)

        self.assertEqual(mapped, [{"id": 100, "value": [800]}])
        self.assertEqual(
            restricted,
            [
                {"name": "Максимальный уровень звука/шума", "value": "60 дБ"},
                {"name": "Скорость воздушного потока", "value": "150 м/с"},
            ],
        )

        description = build_description_with_restricted_charcs("Описание", restricted)
        self.assertIn("Максимальный уровень звука/шума: 60 дБ", description)
        self.assertIn("Скорость воздушного потока: 150 м/с", description)

    def test_description_limit_reserves_space_for_restricted_values(self):
        restricted = [{"name": "Скорость", "value": "150 м/с"}]

        description = build_description_with_restricted_charcs("A" * 5000, restricted)

        self.assertLessEqual(len(description), 5000)
        self.assertTrue(description.endswith("Характеристики товара:\nСкорость: 150 м/с"))


if __name__ == "__main__":
    unittest.main()

