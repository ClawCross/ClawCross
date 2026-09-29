export interface OasisTopicPost {
  id: number;
  author: string;
  content: string;
  reply_to?: number | null;
  upvotes: number;
  downvotes: number;
  timestamp: number;
  elapsed: number;
}

export interface OasisTimelineEvent {
  elapsed: number;
  event: string;
  agent?: string;
  detail?: string;
}

export interface OasisTopicDetail {
  topic_id: string;
  question: string;
  user_id: string;
  status: string;
  current_round: number;
  max_rounds: number;
  posts: OasisTopicPost[];
  timeline: OasisTimelineEvent[];
  discussion: boolean;
  conclusion?: string | null;
}

export interface OasisTownRuntimeApi {
  mount(container: HTMLElement, detail: OasisTopicDetail): void;
  update(detail: OasisTopicDetail): void;
  destroy(): void;
}
