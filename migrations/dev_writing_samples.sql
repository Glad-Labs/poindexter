--
-- PostgreSQL database dump
--

\restrict T8X9SKqf1Hq5clg1VcdXczFA4VW2rOCIiHHhqn1KTw5hFgFY0sIzZXjX5OImi0X

-- Dumped from database version 18.1
-- Dumped by pg_dump version 18.1

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: writing_samples; Type: TABLE DATA; Schema: public; Owner: postgres
--

INSERT INTO public.writing_samples (id, user_id, title, description, content, is_active, word_count, char_count, metadata, created_at, updated_at) VALUES (2, 'dev-user-123', 'My Writing Style', 'A sample of my technical writing style', 'The intersection of technology and human creativity has always fascinated me. When I write about complex technical concepts, I believe in breaking them down into simple, relatable terms that resonate with diverse audiences. I prefer to use concrete examples rather than abstract theories, and I always try to tell a story that connects the dots for my readers.', true, 58, 360, '{}', '2026-01-08 20:43:34.844725-05', '2026-01-08 20:43:34.844725-05');
INSERT INTO public.writing_samples (id, user_id, title, description, content, is_active, word_count, char_count, metadata, created_at, updated_at) VALUES (3, 'mock_user_12345', 'sample story doc', 'sample of a story I wrote once long ago...', 'Henry Davis raced past the stop sign, trying his hardest to impress Sarah with his driving. His car jumped and jolted around as he pushed his limits in his 1970 Ford pickup. Almost to his house, Henry could see the grille out the corner of his eye, but it was too late. 
Waking up to the unfamiliar smell of scrubs and pain killers, Henry looked around and noticed that he was in a hospital bed. What happened? He remembered the accident and in a sudden rush of anxiety and unease he thought about Sarah. Quickly looking to his left, he saw her, resting her head against the wall asleep. 
“Sarah, are you okay?” Henry lightly whispered.
“…huh, oh Henry, you’re awake,” sarah said with a yawn, dropping her small handkerchief on the ground by the chair. Henry was so relieved to see that Sarah was okay, he missed one thing. As he went to stand, he noticed that he couldn’t, his legs weren’t there. 
“What the…,” Henry said in shock.
“Your legs, they had to be amputated after the car hit us, they were too badly injured to save.”
“But I’ll never be able to walk again, oh no.”
At that time, a tall man walked in the room, dressed in a long blue jacket and matching blue pants, with a stethoscope draped over his neck, and was carrying a clipboard, with no pen or paper on it. There was something about the man, the way he slid into the room, and the posture of him made the occupants sit uneasy. 
“Excuse me, miss, do you mind if I have some time alone with Mr. Davis?” the man said shrilly across to Sarah.
“Sure,” Sarah replied as she rose and strolled out the door, not paying attention to the fiery eyes looking back at her from the mans pale white face. 
“So, Mr. Davis, I see that you have awoken from your comma. Please, allow me to fill you in. Your accident was very serious, crushing both your legs, leaving them inoperable and forcing us to amputate them. Fortunately, nothing else was injured, from the correct use of your seatbelt, and Miss Sarah was also spared from major injury...”
“Yes, I see that,” smartly stated Henry, who was oddly humored by the man’s appearance. 
“Yes, I’m glad that you find that amusing, but you see, I don’t believe that Sarah does.” 
“What’s that supposed to mean? What does she have to do with this?” Henry’s voice showed his annoyance now with the man, his medicine was beginning to wear off, and the pains of his injuries began to bother him. 
“It is obvious that you have a love for Sarah, and that is the very reason that you are here. You wouldn’t want to loose any chance of her feeling the same, now would you?” 
Henry hadn’t thought of that. It was true, he had always had an attraction for Sarah, and he would give anything for her to feel the same. But what would she think of a gimp? She could never love a man without his legs. Who would ever devote their life to the care of a freak? Certainly not Sarah. Thoughts raced through Henry’s head, confusing him, this of which the man took note of. 
“Who are you?” Henry finally asked after the few moments of silence.
“That is of no importance of who I am, but who you will be to Sarah with your lost abilities. But, I can make you a deal…”
“A deal?” Henry was intrigued. “A deal of what kind?” 
“A trade,” the man whispered in a sinister tone. His eyes became a deeper maroon color and glowed in the staleness of the room. “A working pair of legs, even better that before, for the one thing you value most.”
Of course, thought Henry. He would more than gladly give up his most valuable possessions for the chance for happiness once again with Sarah. 
“So…”
“Ok, I’ll do it,” said Henry in a confident tone. He was more than ready for this opportunity to change things back.
“Deal,” said the man as he stretched out his arm to shake hands with Henry.
Waking up to the unfamiliar smell of soaps and the staleness of the air, Henry looked around and noticed that he was in a hospital bed. He remembered the deal and in a sudden rush of joy and ecstasy jumped to his feet, and looked around for Sarah. She wasn’t there. He looked over at the chair where she was seated before the encounter with the man, and slowly paced over to it. Looking down at the floor, Henry saw a small handkerchief trapped under the heavy weight of a doctors’ stethoscope.', true, 792, 4272, '{}', '2026-02-02 18:48:41.063661-05', '2026-02-02 18:48:41.063661-05');


--
-- Name: writing_samples_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.writing_samples_id_seq', 3, true);


--
-- PostgreSQL database dump complete
--

\unrestrict T8X9SKqf1Hq5clg1VcdXczFA4VW2rOCIiHHhqn1KTw5hFgFY0sIzZXjX5OImi0X

