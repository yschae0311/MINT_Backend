한 기사만을 위한 **흑백 신문 스케치** 장면 묘사를 작성합니다.

입력:
- **title**: 기사 제목
- **summary**: 기사 요약(없으면 제목·본문만 사용)
- **body**: 기사 본문 일부

작성 원칙:
- 이미지 생성 모델에 넘길 **영어 장면 설명 2~3문장**만 작성하세요.
- **직설적으로** 그리세요. 은유·상징·추상 풍경·“에너지 전환” 같은 포스터 구도는 금지입니다.
- 제목·요약·본문에 나온 **구체적인 사물·장소·사건**을 영어 명사로 명시하세요. 예: 특정 차종(세단·트럭·로보택시), 공장 라인, 선박, 배터리 셀, 법정 문서, 라이다가 달린 시험 차량.
- 이 기사에서만 보이는 시각적 단서를 **세 가지 이상** 넣으세요. 다른 기사와 바꿔 넣어도 성립하는 장면은 실패입니다.
- 기사가 충전소·충전기가 아니면 charging plaza, charging cables, EV chargers를 넣지 마세요.
- 기사가 전력망이 아니면 power grid, transmission towers, utility poles를 넣지 마세요.
- 실제 인물 얼굴, 회사 로고, 브랜드명, 읽을 수 있는 글자·표지판 금지. 회사는 "an automaker factory", "a robotaxi on a city street"처럼 일반명사로.
- 폭력·선정적 묘사 금지.

나쁜 예: "a metaphorical landscape of the energy transition at dawn"
좋은 예: "A robotaxi sedan with a spinning lidar dome on a wet city intersection at night, faceless pedestrians on the crosswalk, an empty driver seat visible through the windshield."

유효한 JSON만 응답:
{
  "scene": "string — English LITERAL scene of THIS story's subject, not a generic charging plaza"
}
