import unittest

from app.services.report_illustration_service import (
    _SIMPLE_SCENE,
    candidate_image_scenes,
    compose_story_image_prompt,
)
from app.services.story_photo_service import _fallback_story_scene


class StoryIllustrationTests(unittest.TestCase):
    def test_story_scenes_do_not_fall_back_to_charging_plaza(self):
        scenes = candidate_image_scenes("A robotaxi at a night intersection", story=True)
        self.assertEqual(scenes, ["A robotaxi at a night intersection"])
        self.assertNotIn(_SIMPLE_SCENE, scenes)

    def test_report_scenes_still_allow_generic_fallback(self):
        scenes = candidate_image_scenes("policy documents on a desk", story=False)
        self.assertEqual(scenes[0], "policy documents on a desk")
        self.assertEqual(scenes[-1], _SIMPLE_SCENE)

    def test_story_prompt_leads_with_the_article_subject(self):
        scene = "A robotaxi sedan with a spinning lidar dome on a wet city intersection"
        prompt = compose_story_image_prompt(scene)
        self.assertTrue(prompt.startswith(scene))
        self.assertIn("charging plaza", prompt.lower())
        self.assertLess(prompt.lower().find("robotaxi"), prompt.lower().find("charging plaza"))

    def test_fallback_scene_keeps_the_headline(self):
        scene = _fallback_story_scene(
            "웨이모, 샌프란시스코 로보택시 야간 운행 확대",
            "밤 교차로에서 무인 로보택시 운행을 늘린다.",
            "",
        )
        self.assertIn("웨이모", scene)
        self.assertIn("로보택시", scene)
        self.assertIn("not a generic charging station", scene)
