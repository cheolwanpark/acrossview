


<video src="https://github.com/user-attachments/assets/483ecd66-b1d0-4bf0-9f6b-2e80be3c306a" controls width="100%"></video>


# AcrossView

**관점을 연결하다**

> "이 서비스는 관점을 추천하지 않습니다. 관점과 관점 사이를 **연결**합니다."

[FORIF 2025-2 해커톤](https://hforif.notion.site/2025-2-2025-2-2025-12-20-2c3eb3ade64780779eabd7aaa90cd964) 아이디어톤 1위 🏆️

## 팀: MCP는 직접 만들어쓰자

| 이름 | GitHub |
|------|--------|
| 박철완 | [@cheolwanpark](https://github.com/cheolwanpark) |
| 임동현 | [@Happ11quokka](https://github.com/Happ11quokka)|
| 조민성 | [@m-seong0801](https://github.com/m-seong0801)|
| 서지민 | [@mindolok](https://github.com/mindolok) |
| 박혜진 | [@hjpark6463](https://github.com/hjpark6463-cyber) |
| 장준영 | [@junyoung9172](https://github.com/junyoung9172) |
| 심윤성 | [@dbstjd907103](https://github.com/dbstjd907103-cloud) |

---

## 문제: 필터 버블

알고리즘이 내 취향에 맞는 정보만 보여주고, 다른 관점은 배제합니다.

- **유튜브**: 시청 기록 기반으로 비슷한 정치 성향만 추천
- **네이버 뉴스**: 클릭 패턴에 따라 같은 논조만 노출
- **커뮤니티**: 같은 입장 글만 추천, 반대 의견은 보이지 않음

결과적으로 우리는 각자 다른 현실에서 살게 됩니다.
토론 대신 각자 다른 현실, **의견 차이**가 아닌 **의견 분리**.

---

## 솔루션: 쟁점 하이라이팅

AcrossView는 반대 의견을 강요하지 않습니다.
대신, **다양한 관점으로 가는 문**을 만들어 둡니다.

### 작동 방식

1. **쟁점 탐지**: 기사 내 해석이 갈리는 문장 식별
2. **하이라이트 표시**: 확장 가능한 정보가 있음을 시각적으로 암시
3. **클릭 시 전개**: 다양한 관점, 제3의 해석, 객관적 팩트 제공

### 핵심 원칙

- 알고리즘은 **연결의 기회**를 만들고, 최종 선택은 **사용자**가 합니다
- 단순한 "반대 의견"이 아닌 **"이런 관점도 있어요"**
- 설득이 아닌, **노출의 기회**를 만드는 것

---

## 기대 효과: 끊어진 연결 회복

| 연결 | 변화 |
|------|------|
| **맥락 연결** | 반대편이 뭘 근거로 말하는지 이해의 실마리 |
| **대화 연결** | 공격 대신 질문, 승부 대신 이해 |
| **사람 연결** | 상대도 논리가 있다는 인식 |

---

## 빠른 시작

```bash
# 의존성 설치
uv sync

# 환경 변수 설정
cp .env.example .env
# .env 파일에 GEMINI_API_KEY 추가

# 뉴스 크롤링 + 인덱싱
uv run python -m src.main run
uv run python scripts/cli.py index

# 다양한 관점 찾기
uv run python scripts/cli.py find
```

### Chrome 확장 프로그램

```bash
# 서버 실행
uv run python scripts/server.py

# Chrome에서 extension/ 폴더 로드
# chrome://extensions → 개발자 모드 → 압축해제된 확장 프로그램 로드
```

---

## 기술 스택

- **언어**: Python 3.12+
- **AI**: Google Gemini API (임베딩 + LLM)
- **벡터 검색**: sqlite-vss
- **크롤링**: httpx + trafilatura
- **서버**: FastAPI
- **확장**: Chrome Extension (Manifest V3)
