import React, { useEffect, useRef, useState } from 'react';
import {
  Send,
  Bot,
  User,
  Sparkles,
  Loader2,
  Image as ImageIcon,
  Plus,
  Video,
  X,
} from 'lucide-react';
import { apiGet, apiPost, apiPostForm } from '../services/api';
import {
  ChatMessage,
  ChatResponse,
  ChatSessionDetail,
  ChatSessionSummary,
  ChatSource,
  TimelineItemDetail,
  AgentImageResponse,
} from '../types';

const CHAT_SESSION_KEY = 'lifelog.chat.session';
const CHAT_AGENT_PROMPTS_KEY = 'lifelog.chat.agent_prompts';

type AgentSpec = {
  id: string;
  name: string;
  description: string;
  defaultPrompt: string;
  outputLabel: string;
  icon: typeof ImageIcon;
  disabled?: boolean;
  disabledLabel?: string;
};

const buildLocalDate = () => {
  const now = new Date();
  const offset = now.getTimezoneOffset();
  return new Date(now.getTime() - offset * 60 * 1000).toISOString().slice(0, 10);
};

const AGENTS: AgentSpec[] = [
  {
    id: 'daily_cartoon',
    name: 'Cartoon Day Summary',
    description: 'Generate a playful cartoon scene prompt that captures the day.',
    defaultPrompt:
      'Create a whimsical cartoon illustration that summarizes my day on {date}. Highlight the main activities, mood, and setting. Return a concise image prompt and a one-line caption.',
    outputLabel: 'Image prompt',
    icon: ImageIcon,
  },
  {
    id: 'daily_vlog',
    name: 'Daily Vlog Plan',
    description: 'Draft a short vlog outline using memories from the day.',
    defaultPrompt:
      'Create a 45-60 second vlog plan for {date}. Provide a title, 5-7 shot list, a one-line narration hook, and suggested music mood.',
    outputLabel: 'Vlog outline',
    icon: Video,
    disabled: true,
    disabledLabel: 'Post-MVP',
  },
  {
    id: 'daily_insights',
    name: 'Day Insights Infographic',
    description: 'Summarize the day with stats, themes, and a surprise moment.',
    defaultPrompt:
      'Create a daily insights report for {date}. Include key stats, top keywords, labels, and a surprise detail. Return an infographic image prompt.',
    outputLabel: 'Infographic',
    icon: Sparkles,
  },
];

const dedupeSources = (sources: ChatSource[]) => {
  const seenItems = new Set<string>();
  const seenContexts = new Set<string>();
  return sources.filter((source) => {
    if (source.source_item_id) {
      if (seenItems.has(source.source_item_id)) {
        return false;
      }
      seenItems.add(source.source_item_id);
      return true;
    }
    if (seenContexts.has(source.context_id)) {
      return false;
    }
    seenContexts.add(source.context_id);
    return true;
  });
};

export const ChatInterface: React.FC = () => {
  const buildWelcomeMessage = (): ChatMessage => ({
    id: 'welcome',
    role: 'assistant',
    content: "Hi there! I'm OmniMemory. I've analyzed your photos and logs. Ask me anything about your memories!",
    timestamp: new Date(),
  });

  const [messages, setMessages] = useState<ChatMessage[]>([buildWelcomeMessage()]);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem(CHAT_SESSION_KEY);
  });
  const [selectedImage, setSelectedImage] = useState<File | null>(null);
  const [selectedImagePreview, setSelectedImagePreview] = useState<string | null>(null);
  const [agentDate, setAgentDate] = useState(buildLocalDate);
  const [agentPromptOverrides, setAgentPromptOverrides] = useState<Record<string, string>>(() => {
    if (typeof window === 'undefined') return {};
    try {
      const raw = localStorage.getItem(CHAT_AGENT_PROMPTS_KEY);
      return raw ? (JSON.parse(raw) as Record<string, string>) : {};
    } catch (err) {
      console.error('Failed to load agent prompts', err);
      return {};
    }
  });
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [agentPromptDraft, setAgentPromptDraft] = useState('');
  const [previewAttachment, setPreviewAttachment] = useState<{
    url: string;
    name: string;
  } | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const objectUrlsRef = useRef<string[]>([]);

  const activeSession = sessions.find((session) => session.session_id === sessionId) || null;

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    let mounted = true;
    const loadSessions = async () => {
      try {
        const data = await apiGet<ChatSessionSummary[]>('/chat/sessions');
        if (!mounted) return;
        setSessions(data);
        if (data.length === 0) {
          setMessages([buildWelcomeMessage()]);
          return;
        }
        const storedId = sessionId;
        const matching = storedId && data.find((session) => session.session_id === storedId);
        if (matching) {
          await loadSession(storedId);
        } else {
          await loadSession(data[0].session_id);
        }
      } catch (err) {
        console.error(err);
      }
    };
    loadSessions();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    return () => {
      objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      objectUrlsRef.current = [];
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      localStorage.setItem(CHAT_AGENT_PROMPTS_KEY, JSON.stringify(agentPromptOverrides));
    } catch (err) {
      console.error('Failed to save agent prompts', err);
    }
  }, [agentPromptOverrides]);

  const loadSession = async (targetId: string) => {
    setLoadingHistory(true);
    try {
      const detail = await apiGet<ChatSessionDetail>(`/chat/sessions/${targetId}`);
      const loaded = detail.messages
        .filter((msg) => msg.role !== 'system')
        .map((msg) => ({
          id: msg.id,
          role: msg.role,
          content: msg.content,
          timestamp: new Date(msg.created_at),
          sources: msg.sources,
          attachments: msg.attachments,
        }));
      setMessages(loaded.length ? loaded : [buildWelcomeMessage()]);
      setSessionId(detail.session_id);
      localStorage.setItem(CHAT_SESSION_KEY, detail.session_id);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingHistory(false);
    }
  };

  const refreshSessions = async () => {
    try {
      const data = await apiGet<ChatSessionSummary[]>('/chat/sessions');
      setSessions(data);
    } catch (err) {
      console.error(err);
    }
  };

  const handleNewChat = () => {
    setSessionId(null);
    localStorage.removeItem(CHAT_SESSION_KEY);
    setMessages([buildWelcomeMessage()]);
    setSelectedImage(null);
    setSelectedImagePreview(null);
  };

  const handleSelectImage = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    if (!file.type.startsWith('image/')) {
      return;
    }
    const previewUrl = URL.createObjectURL(file);
    objectUrlsRef.current.push(previewUrl);
    setSelectedImage(file);
    setSelectedImagePreview(previewUrl);
    event.target.value = '';
  };

  const handleRemoveImage = () => {
    if (selectedImagePreview) {
      URL.revokeObjectURL(selectedImagePreview);
    }
    setSelectedImage(null);
    setSelectedImagePreview(null);
  };

  const handleSourceClick = async (source: ChatSource) => {
    if (!source.source_item_id) {
      return;
    }
    let anchorDate = source.timestamp ?? undefined;
    if (!anchorDate) {
      try {
        const detail = await apiGet<TimelineItemDetail>(`/timeline/items/${source.source_item_id}`);
        anchorDate = detail.captured_at || anchorDate;
      } catch (err) {
        console.error('Failed to load memory date for timeline focus', err);
      }
    }
    const focus: {
      itemId: string;
      episodeContextId: string;
      viewMode: 'day';
      anchorDate?: string;
    } = {
      itemId: source.source_item_id,
      episodeContextId: source.context_id,
      viewMode: 'day',
    };
    if (anchorDate) {
      focus.anchorDate = anchorDate;
    }
    window.dispatchEvent(
      new CustomEvent('lifelog:timeline-focus', {
        detail: focus,
      })
    );
  };

  const resolveAgentPrompt = (agentId: string) => {
    const agent = AGENTS.find((item) => item.id === agentId);
    if (!agent) {
      return '';
    }
    return agentPromptOverrides[agentId] || agent.defaultPrompt;
  };

  const openAgentPromptEditor = (agentId: string) => {
    setEditingAgentId(agentId);
    setAgentPromptDraft(resolveAgentPrompt(agentId));
  };

  const closeAgentPromptEditor = () => {
    setEditingAgentId(null);
    setAgentPromptDraft('');
  };

  const saveAgentPrompt = () => {
    if (!editingAgentId) {
      return;
    }
    const cleaned = agentPromptDraft.trim();
    setAgentPromptOverrides((prev) => {
      const next = { ...prev };
      if (cleaned) {
        next[editingAgentId] = cleaned;
      } else {
        delete next[editingAgentId];
      }
      return next;
    });
    closeAgentPromptEditor();
  };

  const resetAgentPrompt = () => {
    if (!editingAgentId) {
      return;
    }
    const agent = AGENTS.find((item) => item.id === editingAgentId);
    setAgentPromptDraft(agent?.defaultPrompt || '');
  };

  const sendMessage = async ({
    messageText,
    imageFile,
    imagePreview,
  }: {
    messageText: string;
    imageFile?: File | null;
    imagePreview?: string | null;
  }) => {
    if ((!messageText.trim() && !imageFile) || loading) return;

    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: messageText || (imageFile ? 'Shared a photo.' : ''),
      timestamp: new Date(),
      attachments: imagePreview
        ? [
            {
              id: `local-${Date.now()}`,
              url: imagePreview,
              content_type: imageFile?.type,
              created_at: new Date().toISOString(),
            },
          ]
        : undefined,
    };

    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      let response: ChatResponse;
      if (imageFile) {
        const form = new FormData();
        form.append('message', messageText);
        if (sessionId) {
          form.append('session_id', sessionId);
        }
        form.append('tz_offset_minutes', new Date().getTimezoneOffset().toString());
        form.append('image', imageFile);
        response = await apiPostForm<ChatResponse>('/chat/image', form);
      } else {
        response = await apiPost<ChatResponse>('/chat', {
          message: messageText,
          session_id: sessionId || undefined,
          tz_offset_minutes: new Date().getTimezoneOffset(),
        });
      }

      if (!sessionId || sessionId !== response.session_id) {
        setSessionId(response.session_id);
        localStorage.setItem(CHAT_SESSION_KEY, response.session_id);
      }

      const aiMsg: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: response.message,
        timestamp: new Date(),
        sources: response.sources,
      };

      setMessages((prev) => [...prev, aiMsg]);
      await refreshSessions();
    } catch (err) {
      console.error(err);
      const errorMsg: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: "I'm having trouble connecting right now. Please try again.",
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handleSend = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if ((!input.trim() && !selectedImage) || loading) return;

    const imageFile = selectedImage;
    const imagePreview = selectedImagePreview;
    const messageText = input.trim();

    setInput('');
    setSelectedImage(null);
    setSelectedImagePreview(null);

    await sendMessage({ messageText, imageFile, imagePreview });
  };

  const handleAgentRun = async (agentId: string) => {
    const agent = AGENTS.find((item) => item.id === agentId);
    if (!agent || agent.disabled || loading) {
      return;
    }
    const rawPrompt = resolveAgentPrompt(agentId);
    const resolvedPrompt = rawPrompt.replace(/\{date\}/g, agentDate);
    const finalPrompt = resolvedPrompt.includes(agentDate)
      ? resolvedPrompt
      : `${resolvedPrompt}\n\nUse memories from ${agentDate} only.`;

    if (agent.id === 'daily_cartoon') {
      const userMsg: ChatMessage = {
        id: Date.now().toString(),
        role: 'user',
        content: `Run agent: ${agent.name} (${agentDate})`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setLoading(true);
      try {
        const response = await apiPost<AgentImageResponse>('/chat/agents/cartoon', {
          prompt: finalPrompt,
          date: agentDate,
          session_id: sessionId || undefined,
          tz_offset_minutes: new Date().getTimezoneOffset(),
        });
        if (!sessionId || sessionId !== response.session_id) {
          setSessionId(response.session_id);
          localStorage.setItem(CHAT_SESSION_KEY, response.session_id);
        }
        const aiMsg: ChatMessage = {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: response.message,
          timestamp: new Date(),
          attachments: response.attachments,
        };
        setMessages((prev) => [...prev, aiMsg]);
        await refreshSessions();
      } catch (err) {
        console.error(err);
        const errorMsg: ChatMessage = {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: "I'm having trouble generating that image right now.",
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errorMsg]);
      } finally {
        setLoading(false);
      }
      return;
    }

    if (agent.id === 'daily_insights') {
      const userMsg: ChatMessage = {
        id: Date.now().toString(),
        role: 'user',
        content: `Run agent: ${agent.name} (${agentDate})`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setLoading(true);
      try {
        const response = await apiPost<AgentImageResponse>('/chat/agents/insights', {
          prompt: finalPrompt,
          date: agentDate,
          session_id: sessionId || undefined,
          tz_offset_minutes: new Date().getTimezoneOffset(),
          include_image: true,
        });
        if (!sessionId || sessionId !== response.session_id) {
          setSessionId(response.session_id);
          localStorage.setItem(CHAT_SESSION_KEY, response.session_id);
        }
        const aiMsg: ChatMessage = {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: response.message,
          timestamp: new Date(),
          attachments: response.attachments,
        };
        setMessages((prev) => [...prev, aiMsg]);
        await refreshSessions();
      } catch (err) {
        console.error(err);
        const errorMsg: ChatMessage = {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: "I'm having trouble generating that insight right now.",
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errorMsg]);
      } finally {
        setLoading(false);
      }
      return;
    }

    const messageText = `[Agent: ${agent.name}]\n${finalPrompt}`;
    await sendMessage({ messageText });
  };

  const editingAgent = editingAgentId
    ? AGENTS.find((item) => item.id === editingAgentId) || null
    : null;

  const buildAttachmentName = (url: string, fallback: string) => {
    try {
      const parsed = new URL(url);
      const name = parsed.pathname.split('/').pop();
      if (name) {
        return name;
      }
    } catch (err) {
      console.error('Failed to parse attachment URL', err);
    }
    return fallback;
  };

  return (
    <div className="flex h-full bg-slate-50">
      <aside className="w-72 bg-white border-r border-slate-200 flex flex-col">
        <div className="px-4 py-4 border-b border-slate-200 flex items-center justify-between">
          <div>
            <p className="text-xs uppercase tracking-wider text-slate-400">Chat History</p>
            <p className="text-sm font-semibold text-slate-800">Sessions</p>
          </div>
          <button
            type="button"
            onClick={handleNewChat}
            className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700"
          >
            <Plus size={14} />
            New chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {sessions.length === 0 && (
            <p className="text-xs text-slate-400">No chats yet.</p>
          )}
          {sessions.map((session) => {
            const isActive = session.session_id === sessionId;
            return (
              <button
                key={session.session_id}
                type="button"
                onClick={() => loadSession(session.session_id)}
                className={`w-full text-left px-3 py-2 rounded-lg border transition ${
                  isActive
                    ? 'border-primary-300 bg-primary-50 text-slate-900'
                    : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                }`}
              >
                <p className="text-sm font-medium truncate">
                  {session.title || 'Untitled chat'}
                </p>
                <p className="text-[11px] text-slate-400 truncate">
                  {new Date(session.last_message_at).toLocaleDateString()} · {session.message_count} messages
                </p>
              </button>
            );
          })}
        </div>
      </aside>

      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="px-6 py-4 bg-white border-b border-slate-200 flex justify-between items-center shadow-sm z-10">
          <div>
            <h2 className="text-lg font-bold text-slate-800 flex items-center">
              <Sparkles className="w-5 h-5 text-primary-500 mr-2" />
              {activeSession?.title || 'Memory Assistant'}
            </h2>
            <p className="text-xs text-slate-500">Powered by Gemini 2.5</p>
          </div>
          <div className="text-xs text-slate-400">
            Session ID: {sessionId ? sessionId.slice(0, 8) : 'new'}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {loadingHistory && (
            <p className="text-center text-xs text-slate-400">Loading chat history...</p>
          )}
          {messages.map((msg) => {
            const uniqueSources = msg.sources ? dedupeSources(msg.sources) : [];
            return (
              <div
                key={msg.id}
                className={`flex items-start max-w-3xl mx-auto ${
                  msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'
                }`}
              >
            {/* Avatar */}
            <div
              className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
                msg.role === 'user' ? 'bg-indigo-100 ml-3' : 'bg-primary-100 mr-3'
              }`}
            >
              {msg.role === 'user' ? (
                <User size={16} className="text-indigo-600" />
              ) : (
                <Bot size={16} className="text-primary-600" />
              )}
            </div>

            {/* Bubble */}
            <div className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
              {msg.attachments && msg.attachments.length > 0 && (
                <div className="mb-2 flex gap-2 flex-wrap">
                  {msg.attachments.map((attachment) => (
                    <button
                      key={attachment.id}
                      type="button"
                      onClick={() =>
                        setPreviewAttachment({
                          url: attachment.url,
                          name: buildAttachmentName(attachment.url, `attachment-${attachment.id}`),
                        })
                      }
                      className="rounded-xl border border-slate-200 overflow-hidden"
                      title="View image"
                    >
                      <img
                        src={attachment.url}
                        alt="Uploaded"
                        className="w-48 h-32 object-cover cursor-zoom-in"
                      />
                    </button>
                  ))}
                </div>
              )}
              <div
                className={`px-5 py-3.5 rounded-2xl text-sm shadow-sm leading-relaxed whitespace-pre-wrap ${
                  msg.role === 'user'
                    ? 'bg-indigo-600 text-white rounded-tr-none'
                    : 'bg-white text-slate-800 border border-slate-100 rounded-tl-none'
                }`}
              >
                {msg.content}
              </div>
              
              {/* Sources (assistant only) */}
              {msg.role === 'assistant' && uniqueSources.length > 0 && (
                <div className="mt-3 ml-1">
                   <p className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wider">Relevant Memories</p>
                   <div className="flex space-x-3 overflow-x-auto pb-2 no-scrollbar max-w-full">
                     {uniqueSources.map((src) => (
                       <div
                         key={src.context_id}
                         onClick={() => handleSourceClick(src)}
                         className={`flex-shrink-0 w-32 bg-white rounded-lg border border-slate-200 overflow-hidden shadow-sm transition-all ${
                           src.source_item_id ? 'hover:shadow-md cursor-pointer' : 'cursor-default'
                         }`}
                       >
                         <div className="h-20 w-full overflow-hidden bg-slate-100 flex items-center justify-center">
                           {src.thumbnail_url ? (
                             <img src={src.thumbnail_url} alt="source" className="w-full h-full object-cover" />
                           ) : (
                             <ImageIcon size={16} className="text-slate-400" />
                           )}
                         </div>
                         <div className="p-2 bg-slate-50">
                            <p className="text-[10px] text-slate-500 font-medium truncate">
                              {src.timestamp ? new Date(src.timestamp).toLocaleString() : 'Unknown time'}
                            </p>
                            <p className="text-[10px] text-slate-800 truncate">
                              {src.title || src.snippet || 'Memory'}
                            </p>
                         </div>
                       </div>
                     ))}
                   </div>
                </div>
              )}
              
              <span className="text-[10px] text-slate-400 mt-1 mx-1">
                {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>
          </div>
        );
        })}
        {loading && (
          <div className="flex items-start max-w-3xl mx-auto">
             <div className="flex-shrink-0 w-8 h-8 rounded-full bg-primary-100 mr-3 flex items-center justify-center">
                <Bot size={16} className="text-primary-600" />
             </div>
             <div className="px-5 py-4 bg-white rounded-2xl rounded-tl-none shadow-sm border border-slate-100 flex items-center space-x-2">
                <Loader2 className="w-4 h-4 text-primary-500 animate-spin" />
                <span className="text-xs text-slate-500">Thinking...</span>
             </div>
          </div>
        )}
        <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="bg-white border-t border-slate-200 p-4 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)]">
              {selectedImagePreview && (
            <div className="max-w-3xl mx-auto mb-3 flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() =>
                    setPreviewAttachment({
                      url: selectedImagePreview,
                      name: 'selected-image',
                    })
                  }
                  className="rounded-md border border-slate-200 overflow-hidden"
                  title="View image"
                >
                  <img
                    src={selectedImagePreview}
                    alt="Selected"
                    className="w-14 h-14 object-cover cursor-zoom-in"
                  />
                </button>
                <div className="text-xs text-slate-600">
                  <p className="font-medium">Image attached</p>
                  <p className="text-slate-400">Ask a question to find related memories.</p>
                </div>
              </div>
              <button
                type="button"
                onClick={handleRemoveImage}
                className="text-xs text-slate-500 hover:text-slate-700"
              >
                Remove
              </button>
            </div>
          )}
          <form
            onSubmit={handleSend}
            className="max-w-3xl mx-auto relative flex items-center bg-slate-50 rounded-xl border border-slate-200 focus-within:ring-2 focus-within:ring-primary-100 focus-within:border-primary-400 transition-all"
          >
            <button
              type="button"
              className="p-3 text-slate-400 hover:text-primary-600 transition-colors"
              title="Upload image for analysis"
              onClick={() => fileInputRef.current?.click()}
            >
              <ImageIcon size={20} />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              onChange={handleSelectImage}
              className="hidden"
            />
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask 'When was my trip to Kyoto?'..."
              className="flex-1 bg-transparent border-none focus:ring-0 text-slate-800 placeholder-slate-400 text-sm py-3.5"
              disabled={loading}
            />
            <button
              type="submit"
              disabled={(!input.trim() && !selectedImage) || loading}
              className={`p-2 m-1.5 rounded-lg transition-all ${
                (input.trim() || selectedImage) && !loading
                  ? 'bg-primary-600 text-white hover:bg-primary-700 shadow-sm'
                  : 'bg-slate-200 text-slate-400 cursor-not-allowed'
              }`}
            >
              <Send size={18} />
            </button>
          </form>
          <p className="text-center text-[10px] text-slate-400 mt-2">
            OmniMemory may display inaccurate info. Verify important details.
          </p>
        </div>
      </div>

      <aside className="hidden xl:flex w-80 bg-white border-l border-slate-200 flex-col">
        <div className="px-4 py-4 border-b border-slate-200">
          <p className="text-xs uppercase tracking-wider text-slate-400">Studio</p>
          <p className="text-sm font-semibold text-slate-800">Downstream agents</p>
          <div className="mt-3">
            <label className="block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              Target day
            </label>
            <input
              type="date"
              value={agentDate}
              onChange={(event) => setAgentDate(event.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100"
            />
            <p className="mt-1 text-[10px] text-slate-400">Prompts support {`{date}`}.</p>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {AGENTS.map((agent) => {
            const AgentIcon = agent.icon;
            const isCustomized = Boolean(agentPromptOverrides[agent.id]);
            return (
              <div
                key={agent.id}
                className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm ${
                  agent.disabled ? 'opacity-70' : ''
                }`}
              >
                <div className="flex items-start gap-3">
                  <div className="h-10 w-10 rounded-xl bg-slate-100 flex items-center justify-center">
                    <AgentIcon className="h-5 w-5 text-slate-600" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-slate-900">{agent.name}</p>
                    <p className="text-xs text-slate-500 mt-1">{agent.description}</p>
                    <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-slate-400">
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-500">
                        {agent.outputLabel}
                      </span>
                      {isCustomized && (
                        <span className="rounded-full bg-primary-50 px-2 py-0.5 text-primary-600">
                          Custom
                        </span>
                      )}
                      {agent.disabled && agent.disabledLabel && (
                        <span className="rounded-full bg-slate-200 px-2 py-0.5 text-slate-600">
                          {agent.disabledLabel}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleAgentRun(agent.id)}
                    disabled={loading || agent.disabled}
                    className="flex-1 rounded-lg bg-primary-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
                  >
                    {agent.disabled ? 'Disabled' : 'Run'}
                  </button>
                  <button
                    type="button"
                    onClick={() => openAgentPromptEditor(agent.id)}
                    className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:border-slate-300 hover:text-slate-800"
                    disabled={agent.disabled}
                  >
                    Edit prompt
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        <div className="px-4 py-3 border-t border-slate-200 text-[11px] text-slate-400">
          Agents run through the same memory context as chat. Wire in image/video APIs to
          generate media outputs.
        </div>
      </aside>

      {editingAgent && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="w-full max-w-xl rounded-2xl border border-slate-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <p className="text-xs uppercase tracking-wider text-slate-400">Edit agent prompt</p>
                <p className="text-sm font-semibold text-slate-800">{editingAgent.name}</p>
              </div>
              <button
                type="button"
                onClick={closeAgentPromptEditor}
                className="rounded-full p-1 text-slate-400 hover:text-slate-600"
              >
                <X size={16} />
              </button>
            </div>
            <div className="px-5 py-4 space-y-3">
              <textarea
                value={agentPromptDraft}
                onChange={(event) => setAgentPromptDraft(event.target.value)}
                rows={6}
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100"
              />
              <p className="text-[11px] text-slate-400">
                Use {`{date}`} to inject the target day. Prompts are saved per browser.
              </p>
            </div>
            <div className="flex items-center justify-between border-t border-slate-200 px-5 py-4">
              <button
                type="button"
                onClick={resetAgentPrompt}
                className="text-xs font-semibold text-slate-500 hover:text-slate-700"
              >
                Reset to default
              </button>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={closeAgentPromptEditor}
                  className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:border-slate-300 hover:text-slate-800"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={saveAgentPrompt}
                  className="rounded-lg bg-primary-600 px-3 py-2 text-xs font-semibold text-white hover:bg-primary-700"
                >
                  Save prompt
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {previewAttachment && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4">
          <div className="w-full max-w-4xl rounded-2xl border border-slate-200 bg-white shadow-xl overflow-hidden">
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
              <p className="text-sm font-semibold text-slate-800">Image preview</p>
              <button
                type="button"
                onClick={() => setPreviewAttachment(null)}
                className="rounded-full p-1 text-slate-400 hover:text-slate-600"
              >
                <X size={16} />
              </button>
            </div>
            <div className="bg-slate-50 px-5 py-4 flex items-center justify-center">
              <img
                src={previewAttachment.url}
                alt="Preview"
                className="max-h-[70vh] w-auto rounded-xl border border-slate-200"
              />
            </div>
            <div className="flex items-center justify-between border-t border-slate-200 px-5 py-3">
              <a
                href={previewAttachment.url}
                download={previewAttachment.name}
                target="_blank"
                rel="noreferrer"
                className="rounded-lg bg-primary-600 px-3 py-2 text-xs font-semibold text-white hover:bg-primary-700"
              >
                Download
              </a>
              <button
                type="button"
                onClick={() => setPreviewAttachment(null)}
                className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:border-slate-300 hover:text-slate-800"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
