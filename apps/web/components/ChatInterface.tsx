import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { PageMotion } from './PageMotion';
import { useSettings } from '../contexts/SettingsContext';
import { useI18n } from '../i18n/useI18n';
import { addDaysZoned, formatDateKey, getTimeZoneOffsetMinutes } from '../utils/time';
import {
  ChatMessage,
  ChatResponse,
  ChatSessionDetail,
  ChatSessionSummary,
  ChatSource,
  TimelineItemDetail,
  AgentChatResponse,
  AgentImageResponse,
  AgentTextResponse,
} from '../types';
import { toast } from '../services/toast';

const CHAT_SESSION_KEY = 'lifelog.chat.session';
const CHAT_AGENT_PROMPTS_KEY = 'lifelog.chat.agent_prompts';
const CHAT_AGENT_SESSION_KEY = 'lifelog.chat.agent.session';
const CHAT_AGENT_MODE_KEY = 'lifelog.chat.agent.mode';

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

type AgentDateMode = 'single' | 'range' | 'last7' | 'last30' | 'last365';

type AgentDateRange = {
  startDate: string;
  endDate: string;
  label: string;
  mode: AgentDateMode;
};

const buildAgentDateRange = ({
  mode,
  singleDate,
  rangeStart,
  rangeEnd,
  timeZone,
}: {
  mode: AgentDateMode;
  singleDate: string;
  rangeStart: string;
  rangeEnd: string;
  timeZone: string;
}): AgentDateRange => {
  const today = new Date();
  const todayKey = formatDateKey(today, timeZone);
  let startDate = singleDate;
  let endDate = singleDate;

  if (mode === 'range') {
    startDate = rangeStart || singleDate;
    endDate = rangeEnd || rangeStart || singleDate;
  } else if (mode === 'last7') {
    endDate = todayKey;
    startDate = formatDateKey(addDaysZoned(today, -6, timeZone), timeZone);
  } else if (mode === 'last30') {
    endDate = todayKey;
    startDate = formatDateKey(addDaysZoned(today, -29, timeZone), timeZone);
  } else if (mode === 'last365') {
    endDate = todayKey;
    startDate = formatDateKey(addDaysZoned(today, -364, timeZone), timeZone);
  }

  if (endDate < startDate) {
    const temp = startDate;
    startDate = endDate;
    endDate = temp;
  }

  const label = startDate === endDate ? startDate : `${startDate} to ${endDate}`;
  return { startDate, endDate, label, mode };
};

const AGENT_DEFINITIONS: AgentSpec[] = [
  {
    id: 'daily_cartoon',
    name: 'Cartoon Day Summary',
    description: 'Generate a playful cartoon scene prompt that captures the selected range.',
    defaultPrompt:
      'Create a detailed cartoon illustration for {date_range}. Highlight the 2-3 most vivid moments, mood, and setting. Include concrete props, lighting, palette, and background details. Return a rich image prompt and a punchy caption.',
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
    description: 'Summarize the selected range with stats, themes, and highlights.',
    defaultPrompt:
      'Create a detailed daily insights report for {date_range}. Include key stats, top keywords, labels, and trends. Return an infographic image prompt with clear text callouts.',
    outputLabel: 'Infographic',
    icon: Sparkles,
  },
  {
    id: 'daily_surprise',
    name: 'Surprise Me',
    description: 'Surface an unexpected moment or pattern from the selected range.',
    defaultPrompt:
      'Find the most surprising, easy-to-miss detail from {date_range}. Focus on subtle visual cues or background moments people might overlook (clothing, signage, objects, gestures). Avoid generic themes unless anchored to a specific detail. Explain why it stands out, cite concrete details, and include a short list of supporting memory clues.',
    outputLabel: 'Surprise highlight',
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
  const { settings } = useSettings();
  const { t, locale } = useI18n();
  const timeZone = settings.preferences.timezone;
  const buildWelcomeMessage = useCallback(
    (): ChatMessage => ({
      id: 'welcome',
      role: 'assistant',
      content: t(
        "Hi there! I'm OmniMemory. I've analyzed your photos and logs. Ask me anything about your memories!"
      ),
      timestamp: new Date(),
    }),
    [t]
  );

  const agents = useMemo(
    () =>
      AGENT_DEFINITIONS.map((agent) => ({
        ...agent,
        name: t(agent.name),
        description: t(agent.description),
        defaultPrompt: t(agent.defaultPrompt),
        outputLabel: t(agent.outputLabel),
        disabledLabel: agent.disabledLabel ? t(agent.disabledLabel) : undefined,
      })),
    [t]
  );

  const [messages, setMessages] = useState<ChatMessage[]>(() => [buildWelcomeMessage()]);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [sessionOffset, setSessionOffset] = useState(0);
  const [sessionHasMore, setSessionHasMore] = useState(true);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem(CHAT_SESSION_KEY);
  });
  const [agentSessionId, setAgentSessionId] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem(CHAT_AGENT_SESSION_KEY);
  });
  const [agentMode, setAgentMode] = useState(() => {
    if (typeof window === 'undefined') return false;
    return localStorage.getItem(CHAT_AGENT_MODE_KEY) === 'true';
  });
  const [debugMode, setDebugMode] = useState(false);
  const [selectedImage, setSelectedImage] = useState<File | null>(null);
  const [selectedImagePreview, setSelectedImagePreview] = useState<string | null>(null);
  const [agentDate, setAgentDate] = useState(() => formatDateKey(new Date(), timeZone));
  const [agentDateMode, setAgentDateMode] = useState<AgentDateMode>('single');
  const [agentRangeStart, setAgentRangeStart] = useState(() =>
    formatDateKey(new Date(), timeZone)
  );
  const [agentRangeEnd, setAgentRangeEnd] = useState(() => formatDateKey(new Date(), timeZone));
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
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const objectUrlsRef = useRef<string[]>([]);
  const prependGuardRef = useRef(false);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyCursorId, setHistoryCursorId] = useState<string | null>(null);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);

  const activeSession = sessions.find((session) => session.session_id === sessionId) || null;
  const agentRange = useMemo(
    () =>
      buildAgentDateRange({
        mode: agentDateMode,
        singleDate: agentDate,
        rangeStart: agentRangeStart,
        rangeEnd: agentRangeEnd,
        timeZone,
      }),
    [agentDate, agentDateMode, agentRangeEnd, agentRangeStart, timeZone]
  );

  const formatDateLabel = (value: string | Date | null | undefined) => {
    if (!value) return t('Unknown date');
    const parsed = typeof value === 'string' ? new Date(value) : value;
    if (Number.isNaN(parsed.getTime())) {
      return t('Unknown date');
    }
    return new Intl.DateTimeFormat(locale, {
      timeZone,
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    }).format(parsed);
  };

  const formatDateTimeLabel = (value: string | Date | null | undefined) => {
    if (!value) return t('Unknown time');
    const parsed = typeof value === 'string' ? new Date(value) : value;
    if (Number.isNaN(parsed.getTime())) {
      return t('Unknown time');
    }
    return new Intl.DateTimeFormat(locale, {
      timeZone,
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    }).format(parsed);
  };

  const formatTimeLabel = (value: Date) =>
    new Intl.DateTimeFormat(locale, {
      timeZone,
      hour: '2-digit',
      minute: '2-digit',
    }).format(value);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    if (prependGuardRef.current) {
      prependGuardRef.current = false;
      return;
    }
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // eslint-disable-next-line react-hooks/exhaustive-deps -- initial hydration only
  useEffect(() => {
    let mounted = true;
    const sessionPageSize = 20;
    const loadSessions = async () => {
      setLoadingSessions(true);
      try {
        const data = await apiGet<ChatSessionSummary[]>(
          `/chat/sessions?limit=${sessionPageSize}&offset=0`
        );
        if (!mounted) return;
        setSessions(data);
        setSessionOffset(data.length);
        setSessionHasMore(data.length === sessionPageSize);
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
      } finally {
        if (mounted) {
          setLoadingSessions(false);
        }
      }
    };
    loadSessions();
    return () => {
      mounted = false;
    };
  }, []);

  // eslint-disable-next-line react-hooks/exhaustive-deps -- cleanup on unmount only
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

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      localStorage.setItem(CHAT_AGENT_MODE_KEY, agentMode ? 'true' : 'false');
    } catch (err) {
      console.error('Failed to persist agent mode', err);
    }
  }, [agentMode]);

  const loadSession = useCallback(async (targetId: string) => {
    setLoadingHistory(true);
    setHistoryHasMore(false);
    setHistoryCursorId(null);
    try {
      const params = new URLSearchParams();
      params.set('limit', '50');
      if (debugMode) {
        params.set('debug', 'true');
      }
      const detail = await apiGet<ChatSessionDetail>(
        `/chat/sessions/${targetId}?${params.toString()}`
      );
      const loaded = detail.messages
        .filter((msg) => msg.role !== 'system')
        .map((msg) => ({
          id: msg.id,
          role: msg.role,
          content: msg.content,
          timestamp: new Date(msg.created_at),
          sources: msg.sources,
          attachments: msg.attachments,
          telemetry: msg.telemetry,
        }));
      setMessages(loaded.length ? loaded : [buildWelcomeMessage()]);
      setSessionId(detail.session_id);
      setHistoryHasMore(Boolean(detail.has_more));
      setHistoryCursorId(detail.next_before_id ?? null);
      localStorage.setItem(CHAT_SESSION_KEY, detail.session_id);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingHistory(false);
    }
  }, [buildWelcomeMessage, debugMode]);

  const loadOlderMessages = useCallback(async () => {
    if (!sessionId || historyLoadingMore || !historyHasMore || !historyCursorId) {
      return;
    }
    const container = messagesContainerRef.current;
    const prevHeight = container?.scrollHeight ?? 0;
    const prevScrollTop = container?.scrollTop ?? 0;
    setHistoryLoadingMore(true);
    try {
      const params = new URLSearchParams();
      params.set('limit', '50');
      params.set('before_id', historyCursorId);
      if (debugMode) {
        params.set('debug', 'true');
      }
      const detail = await apiGet<ChatSessionDetail>(
        `/chat/sessions/${sessionId}?${params.toString()}`
      );
      const older = detail.messages
        .filter((msg) => msg.role !== 'system')
        .map((msg) => ({
          id: msg.id,
          role: msg.role,
          content: msg.content,
          timestamp: new Date(msg.created_at),
          sources: msg.sources,
          attachments: msg.attachments,
          telemetry: msg.telemetry,
        }));
      if (older.length > 0) {
        prependGuardRef.current = true;
        setMessages((prev) => [...older, ...prev]);
      }
      setHistoryHasMore(Boolean(detail.has_more));
      setHistoryCursorId(detail.next_before_id ?? null);
      if (container && older.length > 0) {
        requestAnimationFrame(() => {
          const nextHeight = container.scrollHeight;
          container.scrollTop = nextHeight - prevHeight + prevScrollTop;
        });
      }
    } catch (err) {
      console.error(err);
    } finally {
      setHistoryLoadingMore(false);
    }
  }, [debugMode, historyCursorId, historyHasMore, historyLoadingMore, sessionId]);

  const handleMessagesScroll = useCallback(() => {
    if (loadingHistory || historyLoadingMore || !historyHasMore) {
      return;
    }
    const container = messagesContainerRef.current;
    if (!container) {
      return;
    }
    if (container.scrollTop <= 120) {
      void loadOlderMessages();
    }
  }, [historyHasMore, historyLoadingMore, loadOlderMessages, loadingHistory]);

  const refreshSessions = useCallback(async () => {
    try {
      const sessionPageSize = 20;
      const data = await apiGet<ChatSessionSummary[]>(
        `/chat/sessions?limit=${sessionPageSize}&offset=0`
      );
      setSessions(data);
      setSessionOffset(data.length);
      setSessionHasMore(data.length === sessionPageSize);
    } catch (err) {
      console.error(err);
    }
  }, []);

  const loadMoreSessions = useCallback(async () => {
    if (loadingSessions || !sessionHasMore) return;
    setLoadingSessions(true);
    try {
      const sessionPageSize = 20;
      const data = await apiGet<ChatSessionSummary[]>(
        `/chat/sessions?limit=${sessionPageSize}&offset=${sessionOffset}`
      );
      setSessions((prev) => [...prev, ...data]);
      setSessionOffset((prev) => prev + data.length);
      setSessionHasMore(data.length === sessionPageSize);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingSessions(false);
    }
  }, [loadingSessions, sessionHasMore, sessionOffset]);

  const handleNewChat = () => {
    setSessionId(null);
    localStorage.removeItem(CHAT_SESSION_KEY);
    setAgentSessionId(null);
    localStorage.removeItem(CHAT_AGENT_SESSION_KEY);
    setMessages([buildWelcomeMessage()]);
    setSelectedImage(null);
    setSelectedImagePreview(null);
  };

  const handleRemoveImage = useCallback(() => {
    if (selectedImagePreview) {
      URL.revokeObjectURL(selectedImagePreview);
    }
    setSelectedImage(null);
    setSelectedImagePreview(null);
  }, [selectedImagePreview]);

  useEffect(() => {
    if (agentMode && selectedImage) {
      handleRemoveImage();
    }
  }, [agentMode, selectedImage, handleRemoveImage]);

  useEffect(() => {
    if (sessionId && !agentMode) {
      void loadSession(sessionId);
    }
  }, [agentMode, debugMode, loadSession, sessionId]);

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

  const handleSourceClick = async (source: ChatSource) => {
    let anchorDate = source.timestamp ?? undefined;
    const itemId = source.source_item_id || undefined;
    if (itemId) {
      try {
        const detail = await apiGet<TimelineItemDetail>(`/timeline/items/${itemId}`);
        anchorDate = detail.captured_at || anchorDate;
      } catch (err) {
        console.error('Failed to load memory date for timeline focus', err);
      }
    }
    const focus: {
      itemId?: string;
      episodeContextId?: string;
      episodeId?: string;
      viewMode: 'day';
      anchorDate?: string;
    } = {
      viewMode: 'day',
    };
    if (itemId) {
      focus.itemId = itemId;
    }
    if (source.is_episode && source.context_id) {
      focus.episodeContextId = source.context_id;
    }
    // Include episode_id for direct matching (more reliable than context_id)
    if (source.episode_id) {
      focus.episodeId = source.episode_id;
    }
    if (anchorDate) {
      focus.anchorDate = anchorDate;
    }
    if (!focus.itemId && !focus.episodeContextId && !focus.episodeId) {
      return;
    }
    window.dispatchEvent(
      new CustomEvent('lifelog:timeline-focus', {
        detail: focus,
      })
    );
  };

  const resolveAgentPrompt = (agentId: string) => {
    const agent = agents.find((item) => item.id === agentId);
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
    const agent = agents.find((item) => item.id === editingAgentId);
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
    if (agentMode && imageFile) {
      toast.error(t('Agent mode does not support images yet.'), t('Disable agent mode to send images.'));
      return;
    }

    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: messageText || (imageFile ? t('Shared a photo.') : ''),
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
        form.append('tz_offset_minutes', getTimeZoneOffsetMinutes(new Date(), timeZone).toString());
        form.append('debug', debugMode ? 'true' : 'false');
        form.append('image', imageFile);
        response = await apiPostForm<ChatResponse>('/chat/image', form);
      } else if (agentMode) {
        const agentResponse = await apiPost<AgentChatResponse>('/agent/chat', {
          message: messageText,
          session_id: agentSessionId || undefined,
          tz_offset_minutes: getTimeZoneOffsetMinutes(new Date(), timeZone),
          debug: debugMode,
        });
        if (!agentSessionId || agentSessionId !== agentResponse.session_id) {
          setAgentSessionId(agentResponse.session_id);
          localStorage.setItem(CHAT_AGENT_SESSION_KEY, agentResponse.session_id);
        }
        if (!sessionId || sessionId !== agentResponse.session_id) {
          setSessionId(agentResponse.session_id);
          localStorage.setItem(CHAT_SESSION_KEY, agentResponse.session_id);
        }
        const aiMsg: ChatMessage = {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: agentResponse.message,
          timestamp: new Date(),
          sources: agentResponse.sources,
          telemetry: agentResponse.debug ?? undefined,
        };
        setMessages((prev) => [...prev, aiMsg]);
        await refreshSessions();
        setLoading(false);
        return;
      } else {
        response = await apiPost<ChatResponse>('/chat', {
          message: messageText,
          session_id: sessionId || undefined,
          tz_offset_minutes: getTimeZoneOffsetMinutes(new Date(), timeZone),
          debug: debugMode,
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
        telemetry: response.debug ?? undefined,
      };

      setMessages((prev) => [...prev, aiMsg]);
      await refreshSessions();
    } catch (err) {
      console.error(err);
      const errorMessage =
        err instanceof Error && err.message ? err.message : t('Please try again.');
      const errorMsg: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: debugMode
          ? t("Request failed: {message}", { message: errorMessage })
          : t("I'm having trouble connecting right now. Please try again."),
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

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const isMeta = event.metaKey || event.ctrlKey;
      if (!isMeta) return;
      const key = event.key.toLowerCase();
      if (key === 'k') {
        event.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
      if (key === 'enter' && document.activeElement === inputRef.current) {
        event.preventDefault();
        void handleSend();
      }
    };
    window.addEventListener('keydown', handleShortcut);
    return () => {
      window.removeEventListener('keydown', handleShortcut);
    };
  }, [handleSend]);

  const handleAgentRun = async (agentId: string) => {
    const agent = agents.find((item) => item.id === agentId);
    if (!agent || agent.disabled || loading) {
      return;
    }
    const rawPrompt = resolveAgentPrompt(agentId);
    const { startDate, endDate, label } = agentRange;
    let resolvedPrompt = rawPrompt;
    resolvedPrompt = resolvedPrompt.replace(/\{date\}/g, label);
    resolvedPrompt = resolvedPrompt.replace(/\{start_date\}/g, startDate);
    resolvedPrompt = resolvedPrompt.replace(/\{end_date\}/g, endDate);
    resolvedPrompt = resolvedPrompt.replace(/\{date_range\}/g, label);
    const finalPrompt = resolvedPrompt.includes(label)
      ? resolvedPrompt
      : `${resolvedPrompt}\n\nUse memories from ${label} only.`;

    if (agent.id === 'daily_cartoon') {
      const userMsg: ChatMessage = {
        id: Date.now().toString(),
        role: 'user',
        content: t('Run agent: {name} ({date})', { name: agent.name, date: label }),
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setLoading(true);
      try {
        const response = await apiPost<AgentImageResponse>('/chat/agents/cartoon', {
          prompt: finalPrompt,
          date: startDate,
          end_date: startDate !== endDate ? endDate : undefined,
          session_id: sessionId || undefined,
          tz_offset_minutes: getTimeZoneOffsetMinutes(new Date(), timeZone),
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
          sources: response.sources,
        };
        setMessages((prev) => [...prev, aiMsg]);
        await refreshSessions();
      } catch (err) {
        console.error(err);
        const errorMsg: ChatMessage = {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: t("I'm having trouble generating that image right now."),
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
        content: t('Run agent: {name} ({date})', { name: agent.name, date: label }),
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setLoading(true);
      try {
        const response = await apiPost<AgentImageResponse>('/chat/agents/insights', {
          prompt: finalPrompt,
          date: startDate,
          end_date: startDate !== endDate ? endDate : undefined,
          session_id: sessionId || undefined,
          tz_offset_minutes: getTimeZoneOffsetMinutes(new Date(), timeZone),
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
          sources: response.sources,
        };
        setMessages((prev) => [...prev, aiMsg]);
        await refreshSessions();
      } catch (err) {
        console.error(err);
        const errorMsg: ChatMessage = {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: t("I'm having trouble generating that insight right now."),
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errorMsg]);
      } finally {
        setLoading(false);
      }
      return;
    }

    if (agent.id === 'daily_surprise') {
      const userMsg: ChatMessage = {
        id: Date.now().toString(),
        role: 'user',
        content: t('Run agent: {name} ({date})', { name: agent.name, date: label }),
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setLoading(true);
      try {
        const response = await apiPost<AgentTextResponse>('/chat/agents/surprise', {
          prompt: finalPrompt,
          date: startDate,
          end_date: startDate !== endDate ? endDate : undefined,
          session_id: sessionId || undefined,
          tz_offset_minutes: getTimeZoneOffsetMinutes(new Date(), timeZone),
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
          sources: response.sources,
        };
        setMessages((prev) => [...prev, aiMsg]);
        await refreshSessions();
      } catch (err) {
        console.error(err);
        const errorMsg: ChatMessage = {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: t("I'm having trouble generating that surprise right now."),
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errorMsg]);
      } finally {
        setLoading(false);
      }
      return;
    }

    const messageText = `${t('[Agent: {name}]', { name: agent.name })}\n${finalPrompt}`;
    await sendMessage({ messageText });
  };

  const editingAgent = editingAgentId
    ? agents.find((item) => item.id === editingAgentId) || null
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
    <PageMotion className="flex h-full bg-slate-50">
      <aside className="w-72 bg-white border-r border-slate-200 flex flex-col">
        <div className="px-4 py-4 border-b border-slate-200 flex items-center justify-between">
          <div>
            <p className="text-xs uppercase tracking-wider text-slate-400">{t('Chat History')}</p>
            <p className="text-sm font-semibold text-slate-800">{t('Sessions')}</p>
          </div>
          <button
            type="button"
            onClick={handleNewChat}
            className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700"
          >
            <Plus size={14} />
            {t('New chat')}
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {sessions.length === 0 && (
            <p className="text-xs text-slate-400">{t('No chats yet.')}</p>
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
                  {session.title || t('Untitled chat')}
                </p>
                <p className="text-[11px] text-slate-400 truncate">
                  {formatDateLabel(session.last_message_at)} · {t('{count} messages', {
                    count: session.message_count,
                  })}
                </p>
              </button>
            );
          })}
          {sessionHasMore && (
            <button
              type="button"
              onClick={loadMoreSessions}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-[11px] font-semibold text-slate-600 hover:border-slate-300 hover:text-slate-800"
              disabled={loadingSessions}
            >
              {loadingSessions ? t('Loading...') : t('Load more')}
            </button>
          )}
        </div>
      </aside>

      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="px-6 py-4 bg-white border-b border-slate-200 flex justify-between items-center shadow-sm z-10">
          <div>
            <h2 className="text-lg font-bold text-slate-800 flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-primary-500 mr-2" />
              {activeSession?.title || t('Memory Assistant')}
              {agentMode && (
                <span className="rounded-full bg-primary-50 px-2 py-0.5 text-[10px] font-semibold text-primary-700">
                  {t('Agent mode')}
                </span>
              )}
            </h2>
            <p className="text-xs text-slate-500">{t('Powered by Gemini 2.5')}</p>
          </div>
          <div className="text-xs text-slate-400">
            {t('Session ID: {id}', { id: sessionId ? sessionId.slice(0, 8) : t('new') })}
          </div>
        </div>

        {/* Messages */}
        <div
          ref={messagesContainerRef}
          onScroll={handleMessagesScroll}
          className="flex-1 overflow-y-auto p-4 space-y-6"
        >
          {historyLoadingMore && (
            <p className="text-center text-xs text-slate-400">{t('Loading older messages...')}</p>
          )}
          {loadingHistory && (
            <p className="text-center text-xs text-slate-400">{t('Loading chat history...')}</p>
          )}
          {messages.map((msg) => {
            const uniqueSources = msg.sources ? dedupeSources(msg.sources) : [];
            const orderedSources = [...uniqueSources].sort((a, b) => {
              const aIndex = a.source_index ?? Number.POSITIVE_INFINITY;
              const bIndex = b.source_index ?? Number.POSITIVE_INFINITY;
              if (aIndex === bIndex) return 0;
              return aIndex - bIndex;
            });
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
                      title={t('View image')}
                    >
                      <img
                        src={attachment.url}
                        alt={t('Uploaded')}
                        className="w-48 h-32 object-cover cursor-zoom-in"
                        loading="lazy"
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
              {msg.role === 'assistant' && msg.sources !== undefined && (
                <div className="mt-3 ml-1">
                  <p className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wider">
                    {t('Relevant Memories')}
                  </p>
                  {orderedSources.length > 0 ? (
                    <div className="flex space-x-3 overflow-x-auto pb-2 no-scrollbar max-w-full">
                      {orderedSources.map((src) => (
                        <div
                          key={src.context_id}
                          onClick={() => handleSourceClick(src)}
                          className={`flex-shrink-0 w-32 bg-white rounded-lg border border-slate-200 overflow-hidden shadow-sm transition-all ${
                            src.source_item_id ? 'hover:shadow-md cursor-pointer' : 'cursor-default'
                          }`}
                        >
                          <div className="relative h-20 w-full overflow-hidden bg-slate-100 flex items-center justify-center">
                            {src.thumbnail_url ? (
                              <img
                                src={src.thumbnail_url}
                                alt={t('Source')}
                                className="w-full h-full object-cover"
                                loading="lazy"
                              />
                            ) : (
                              <ImageIcon size={16} className="text-slate-400" />
                            )}
                            {src.source_index != null && (
                              <span className="absolute top-1 left-1 rounded-full bg-slate-900/80 text-white text-[10px] px-1.5 py-0.5">
                                #{src.source_index}
                              </span>
                            )}
                          </div>
                          <div className="p-2 bg-slate-50">
                            <p className="text-[10px] text-slate-500 font-medium truncate">
                              {formatDateTimeLabel(src.timestamp)}
                            </p>
                            <p className="text-[10px] text-slate-800 truncate">
                              {src.title || src.snippet || t('Memory')}
                            </p>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[11px] text-slate-400">{t('No relevant memories found.')}</p>
                  )}
                </div>
              )}

              {debugMode && msg.telemetry && (
                <details className="mt-2 ml-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[10px] text-slate-600">
                  <summary className="cursor-pointer font-semibold text-slate-500">
                    {t('Debug details')}
                  </summary>
                  {Array.isArray((msg.telemetry as Record<string, unknown>).events) && (
                    <div className="mt-2">
                      <p className="text-[10px] font-semibold text-slate-500">
                        {t('Agent events')}
                      </p>
                      <ol className="mt-1 space-y-1 text-[10px] text-slate-500">
                        {((msg.telemetry as Record<string, unknown>).events as unknown[]).map(
                          (event, index) => (
                            <li key={`${msg.id}-event-${index}`} className="break-words">
                              {String(event)}
                            </li>
                          )
                        )}
                      </ol>
                    </div>
                  )}
                  <pre className="mt-2 whitespace-pre-wrap break-words text-[10px] text-slate-600">
                    {JSON.stringify(msg.telemetry, null, 2)}
                  </pre>
                </details>
              )}
              
              <span className="text-[10px] text-slate-400 mt-1 mx-1">
                {formatTimeLabel(msg.timestamp)}
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
                <span className="text-xs text-slate-500">{t('Thinking...')}</span>
             </div>
          </div>
        )}
        <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="bg-white border-t border-slate-200 p-4 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)]">
          <div className="max-w-3xl mx-auto mb-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setAgentMode((value) => !value)}
                className={`rounded-full px-3 py-1 text-[11px] font-semibold transition ${
                  agentMode
                    ? 'bg-primary-600 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {agentMode ? t('Agent Mode: On') : t('Agent Mode: Off')}
              </button>
              <button
                type="button"
                onClick={() => setDebugMode((value) => !value)}
                className={`rounded-full px-3 py-1 text-[11px] font-semibold transition ${
                  debugMode
                    ? 'bg-slate-900 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {debugMode ? t('Debug: On') : t('Debug: Off')}
              </button>
            </div>
            <span className="text-slate-400">
              {agentMode ? t('Agent mode uses tools for multi-step queries.') : t('Standard chat mode.')}
            </span>
          </div>
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
                  title={t('View image')}
                >
                  <img
                    src={selectedImagePreview}
                    alt={t('Selected')}
                    className="w-14 h-14 object-cover cursor-zoom-in"
                    loading="lazy"
                  />
                </button>
                <div className="text-xs text-slate-600">
                  <p className="font-medium">{t('Image attached')}</p>
                  <p className="text-slate-400">{t('Ask a question to find related memories.')}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={handleRemoveImage}
                className="text-xs text-slate-500 hover:text-slate-700"
              >
                {t('Remove')}
              </button>
            </div>
          )}
          <form
            onSubmit={handleSend}
            className="max-w-3xl mx-auto relative flex items-center bg-slate-50 rounded-xl border border-slate-200 focus-within:ring-2 focus-within:ring-primary-100 focus-within:border-primary-400 transition-all"
          >
            <button
              type="button"
              className={`p-3 transition-colors ${
                agentMode
                  ? 'text-slate-300 cursor-not-allowed'
                  : 'text-slate-400 hover:text-primary-600'
              }`}
              title={t('Upload image for analysis')}
              onClick={() => fileInputRef.current?.click()}
              disabled={agentMode}
            >
              <ImageIcon size={20} />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              onChange={handleSelectImage}
              className="hidden"
              disabled={agentMode}
            />
            <input
              type="text"
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                agentMode
                  ? t('Ask a multi-step memory question...')
                  : t("Ask 'When was my trip to Kyoto?'...")
              }
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
            {t('OmniMemory may display inaccurate info. Verify important details.')}
          </p>
        </div>
      </div>

      <aside className="hidden xl:flex w-80 bg-white border-l border-slate-200 flex-col">
        <div className="px-4 py-4 border-b border-slate-200">
          <p className="text-xs uppercase tracking-wider text-slate-400">{t('Studio')}</p>
          <p className="text-sm font-semibold text-slate-800">{t('Downstream agents')}</p>
          <div className="mt-3">
            <label className="block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              {t('Target range')}
            </label>
            <select
              value={agentDateMode}
              onChange={(event) => {
                const nextMode = event.target.value as AgentDateMode;
                setAgentDateMode(nextMode);
                if (nextMode === 'range') {
                  setAgentRangeStart((prev) => prev || agentDate);
                  setAgentRangeEnd((prev) => prev || agentDate);
                }
              }}
              className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100"
            >
              <option value="single">{t('Single day')}</option>
              <option value="range">{t('Date range')}</option>
              <option value="last7">{t('Past 7 days')}</option>
              <option value="last30">{t('Past month')}</option>
              <option value="last365">{t('Past year')}</option>
            </select>
            {agentDateMode === 'single' && (
              <input
                type="date"
                value={agentDate}
                onChange={(event) => setAgentDate(event.target.value)}
                className="mt-2 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100"
              />
            )}
            {agentDateMode === 'range' && (
              <div className="mt-2 grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] uppercase tracking-wide text-slate-400">
                    {t('Start')}
                  </label>
                  <input
                    type="date"
                    value={agentRangeStart}
                    onChange={(event) => setAgentRangeStart(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100"
                  />
                </div>
                <div>
                  <label className="block text-[10px] uppercase tracking-wide text-slate-400">
                    {t('End')}
                  </label>
                  <input
                    type="date"
                    value={agentRangeEnd}
                    onChange={(event) => setAgentRangeEnd(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100"
                  />
                </div>
              </div>
            )}
            {agentDateMode !== 'single' && agentDateMode !== 'range' && (
              <p className="mt-2 text-[11px] text-slate-500">
                {t('Using')} {agentRange.label}
              </p>
            )}
            <p className="mt-1 text-[10px] text-slate-400">
              {t('Prompts support {date}, {start_date}, {end_date}, {date_range}.')}
            </p>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {agents.map((agent) => {
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
                          {t('Custom')}
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
                    {agent.disabled ? t('Disabled') : t('Run')}
                  </button>
                  <button
                    type="button"
                    onClick={() => openAgentPromptEditor(agent.id)}
                    className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:border-slate-300 hover:text-slate-800"
                    disabled={agent.disabled}
                  >
                    {t('Edit prompt')}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        <div className="px-4 py-3 border-t border-slate-200 text-[11px] text-slate-400">
          {t(
            'Agents run through the same memory context as chat. Wire in image/video APIs to generate media outputs.'
          )}
        </div>
      </aside>

      {editingAgent && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="w-full max-w-xl rounded-2xl border border-slate-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <p className="text-xs uppercase tracking-wider text-slate-400">
                  {t('Edit agent prompt')}
                </p>
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
                {t(
                  'Use {date}, {start_date}, {end_date}, or {date_range} to inject the target range. Prompts are saved per browser.'
                )}
              </p>
            </div>
            <div className="flex items-center justify-between border-t border-slate-200 px-5 py-4">
              <button
                type="button"
                onClick={resetAgentPrompt}
                className="text-xs font-semibold text-slate-500 hover:text-slate-700"
              >
                {t('Reset to default')}
              </button>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={closeAgentPromptEditor}
                  className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:border-slate-300 hover:text-slate-800"
                >
                  {t('Cancel')}
                </button>
                <button
                  type="button"
                  onClick={saveAgentPrompt}
                  className="rounded-lg bg-primary-600 px-3 py-2 text-xs font-semibold text-white hover:bg-primary-700"
                >
                  {t('Save prompt')}
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
              <p className="text-sm font-semibold text-slate-800">{t('Image preview')}</p>
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
                alt={t('Preview')}
                className="max-h-[70vh] w-auto rounded-xl border border-slate-200"
                loading="lazy"
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
                {t('Download')}
              </a>
              <button
                type="button"
                onClick={() => setPreviewAttachment(null)}
                className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:border-slate-300 hover:text-slate-800"
              >
                {t('Close')}
              </button>
            </div>
          </div>
        </div>
      )}
    </PageMotion>
  );
};
