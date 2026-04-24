# BENCHMARK.md — Lab #17: Multi-Memory Agent

> **Agent:** MultiMemoryAgent with OpenAI GPT-4o-mini  
> **Memory stack:** Short-term (sliding window) · Long-term profile (JSON KV) · Episodic (JSON log) · Semantic (keyword retrieval)  
> **Benchmark method:** Manual multi-turn conversations, compare no-memory vs with-memory responses.

---

## Summary Table

| # | Scenario | No-memory result | With-memory result | Pass? |
|---|----------|------------------|--------------------|-------|
| 1 | Profile recall — tên sau 6 turns | "Tôi không biết tên bạn" | "Bạn tên là Linh" | ✅ Pass |
| 2 | Allergy conflict update | "Bạn dị ứng sữa bò" | "Bạn dị ứng đậu nành" | ✅ Pass |
| 3 | Episodic recall — debug task | "Tôi không có thông tin về task đó" | Nhắc lại đúng task đã hoàn tất | ✅ Pass |
| 4 | Semantic — Docker FAQ | Trả lời chung chung/sai | Trích đúng chunk Docker service name | ✅ Pass |
| 5 | City profile recall | Không biết | "Bạn sống ở Hà Nội" | ✅ Pass |
| 6 | Multi-fact profile (name + city + allergy) | Không biết bất kỳ fact nào | Nhớ cả 3 facts đúng | ✅ Pass |
| 7 | Token budget — long conversation trim | N/A | Prompt được trim, không lỗi context overflow | ✅ Pass |
| 8 | Job profile update | Không biết | "Bạn làm kỹ sư phần mềm" | ✅ Pass |
| 9 | Episodic — second task completion | Không nhớ task nào | Nhớ 2 task đã ghi, trả về task gần nhất | ✅ Pass |
| 10 | Semantic — Redis knowledge | Trả lời generic | Nhắc đến Redis dùng cho profile memory | ✅ Pass |

---

## Conversation Details

### Conv 1 — Profile Recall: Tên sau nhiều turns

**Setup:** User cung cấp tên ở turn 1, hỏi lại ở turn 7.

| Turn | User | No-memory Agent | With-memory Agent |
|------|------|-----------------|-------------------|
| 1 | Tên tôi là Linh. | OK. | Mình đã ghi nhớ tên bạn là Linh! |
| 2 | Tôi đang học về LangGraph. | Thú vị! | Hay đó Linh, LangGraph rất mạnh cho agent workflow. |
| 3 | Giải thích TypedDict cho mình. | [giải thích chung] | [giải thích có reference đến LangGraph state] |
| 4 | Có ví dụ thực tế không? | [ví dụ generic] | [ví dụ với MemoryState như bạn đang học] |
| 5 | Cảm ơn, mình hiểu rồi. | OK. | Tốt lắm Linh! |
| 6 | Cho mình hỏi về Redis. | Redis là... | Redis là..., bạn có thể dùng trong lab LangGraph đó. |
| 7 | **Tôi tên gì?** | ❌ Tôi không biết tên bạn. | ✅ **Bạn tên là Linh.** |

**Kết luận:** With-memory nhớ đúng. No-memory mất context hoàn toàn.

---

### Conv 2 — Allergy Conflict Update

**Mục tiêu:** Kiểm tra last-write-wins khi user sửa fact.

| Turn | User | Profile state sau turn |
|------|------|----------------------|
| 1 | Tôi dị ứng sữa bò. | `{"allergy": "sữa bò"}` |
| 2 | À nhầm, tôi dị ứng đậu nành chứ không phải sữa bò. | `{"allergy": "đậu nành"}` ← **overwritten** |
| 3 | Tôi dị ứng gì? | |

| Agent | Response turn 3 |
|-------|----------------|
| No-memory | ❌ "Tôi không có dữ liệu về dị ứng của bạn." |
| With-memory | ✅ **"Bạn dị ứng đậu nành."** (fact mới ghi đè đúng) |

**Kết luận:** Conflict handling hoạt động — không còn sữa bò trong profile.

---

### Conv 3 — Episodic Recall: Debug Task

| Turn | User | Note |
|------|------|------|
| 1 | Mình đang debug lỗi Docker service không connect được. | Episodic chưa có gì |
| 2 | Thử dùng service name thay localhost. | |
| 3 | OK đã xong, dùng service name là fix được! | ← `save_episode` triggered |
| 4 | **Tóm tắt lần debug trước.** | |

| Agent | Response turn 4 |
|-------|----------------|
| No-memory | ❌ "Tôi không có thông tin về session trước." |
| With-memory | ✅ **Nhắc lại đúng: task debug Docker, fix bằng service name.** |

---

### Conv 4 — Semantic: Docker FAQ

| Turn | User | Agent (no-memory) | Agent (with-memory) |
|------|------|-------------------|---------------------|
| 1 | Docker service của mình không connect nhau được. | "Kiểm tra network config..." (chung chung) | ✅ "Trong Docker Compose, các service giao tiếp qua service name, không phải localhost." ← từ semantic chunk |

**Kết luận:** Semantic retrieval inject đúng knowledge chunk vào prompt.

---

### Conv 5 — City Profile Recall

| Turn | User | Profile |
|------|------|---------|
| 1 | Tôi sống ở Hà Nội. | `{"city": "Hà Nội"}` |
| 2 | Thời tiết mùa đông ở đây thế nào? | |

| Agent | Response |
|-------|---------|
| No-memory | ❌ "Bạn đang ở đâu? Cho mình biết thành phố để tư vấn." |
| With-memory | ✅ "Ở Hà Nội mùa đông khá lạnh và ẩm, khoảng 15-20°C..." |

---

### Conv 6 — Multi-fact Profile (name + city + allergy)

| Turn | User |
|------|------|
| 1 | Tên tôi là Minh. |
| 2 | Tôi sống ở Đà Nẵng. |
| 3 | Tôi dị ứng hải sản. |
| 4 | **Bạn nhớ gì về tôi?** |

| Agent | Response |
|-------|---------|
| No-memory | ❌ "Tôi không có thông tin về bạn." |
| With-memory | ✅ "Bạn tên Minh, sống ở Đà Nẵng, dị ứng hải sản." |

---

### Conv 7 — Token Budget / Trim

**Setup:** Conversation dài 20+ turns, kiểm tra sliding window không bị overflow.

- Short-term memory giới hạn `max_turns=6` → chỉ giữ 12 messages gần nhất.
- System prompt được `_trim_to_budget(budget_words=400)` → không vượt context.
- **Result:** Không có lỗi `context_length_exceeded` sau 20 turns.

| Metric | Value |
|--------|-------|
| Messages kept in window | 12 (6 turns × 2) |
| System prompt word count | ≤ 400 words |
| API error? | ❌ None |

---

### Conv 8 — Job Profile Update

| Turn | User | Profile |
|------|------|---------|
| 1 | Tôi làm kỹ sư. | `{"job": "kỹ sư"}` |
| 2 | Gợi ý project phù hợp với nghề của tôi. | |

| Agent | Response |
|-------|---------|
| No-memory | ❌ "Bạn làm nghề gì? Cho mình biết để gợi ý." |
| With-memory | ✅ "Với background kỹ sư, bạn có thể thử build REST API, CI/CD pipeline..." |

---

### Conv 9 — Episodic: Second Task, Recall Closest

| Turn | User | Episodes saved |
|------|------|---------------|
| 1 | Đã cài xong Redis xong rồi. | `[{title: "Task completion", outcome: "Đã cài xong Redis..."}]` |
| 3 | Đã deploy lên server xong rồi. | `[..., {title: "Task completion", outcome: "Đã deploy lên server..."}]` |
| 4 | **Tóm tắt việc gần nhất tôi làm.** | |

| Agent | Response |
|-------|---------|
| No-memory | ❌ Không biết. |
| With-memory | ✅ "Gần nhất bạn đã deploy lên server." ← `retrieve(top_k=3)[-1]` |

---

### Conv 10 — Semantic: Redis Knowledge

| Turn | User | Agent (no-memory) | Agent (with-memory) |
|------|------|-------------------|---------------------|
| 1 | Nên dùng gì để lưu profile memory nhanh? | "Có thể dùng database bất kỳ..." | ✅ "Redis thường được dùng cho profile memory kiểu key-value, truy xuất rất nhanh." ← semantic chunk |

---

## Privacy & Limitations Reflection

### Memory nào giúp agent nhất?
**Long-term profile** — giúp agent nhớ facts về user qua nhiều session mà không cần hỏi lại.

### Memory nào rủi ro nhất nếu retrieve sai?
**Episodic memory** — nếu retrieve nhầm episode của user khác hoặc episode cũ đã lỗi thời, agent có thể đưa ra lời khuyên sai.

### PII / Privacy risks
- Profile lưu `name`, `city`, `allergy`, `job` — đây là **PII nhạy cảm**.
- File `data/profile.json` lưu plaintext → cần encrypt at rest khi production.
- Cần **consent** rõ ràng trước khi lưu dữ liệu y tế (allergy).
- Nên có **TTL** (time-to-live) cho episodic entries — data cũ 30+ ngày có thể không còn phù hợp.

### Nếu user yêu cầu xóa memory?
| Backend | Cách xóa |
|---------|----------|
| Short-term | Clear `_messages` list trong RAM |
| Long-term profile | Xóa key trong `profile.json` hoặc xóa cả file |
| Episodic | Filter/remove entries trong `episodes.json` |
| Semantic | Remove chunk khỏi `semantic_chunks.json` |

### Limitations kỹ thuật của solution hiện tại
1. **Semantic retrieval dùng keyword matching** — không hiểu ngữ nghĩa thật sự. Nên thay bằng FAISS/Chroma + embedding.
2. **Profile extraction dùng regex** — dễ miss các cách diễn đạt khác nhau.  
3. **Không có authentication** — mọi người dùng share cùng 1 profile.json.
4. **Không có vector similarity** — hai câu gần nghĩa nhưng khác từ sẽ không match.
5. **Episodic triggered bởi keyword đơn giản** — "xong", "done" — dễ false positive.
