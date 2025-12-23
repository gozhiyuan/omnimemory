import React, { useState, useRef, useEffect } from 'react';
import { Send, Bot, User, Sparkles, Loader2, Image as ImageIcon } from 'lucide-react';
import { ChatMessage, MemoryItem } from '../types';
import { sendMessageToGemini } from '../services/geminiService';
import { MOCK_MEMORIES } from '../constants';

export const ChatInterface: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'model',
      content: "Hi there! I'm Lifelog AI. I've analyzed your photos and logs. Ask me anything about your memories!",
      timestamp: new Date(),
    }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!input.trim() || loading) return;

    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      timestamp: new Date(),
    };

    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      // In a real app, this would call our backend which handles the RAG + Gemini call.
      // Here we simulate RAG by passing mock memories directly to the service helper.
      const responseText = await sendMessageToGemini(userMsg.content, MOCK_MEMORIES);
      
      const aiMsg: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'model',
        content: responseText,
        timestamp: new Date(),
        // Mock relevant sources for the UI
        sources: MOCK_MEMORIES.slice(0, 2), 
      };

      setMessages(prev => [...prev, aiMsg]);
    } catch (err) {
      console.error(err);
      const errorMsg: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'model',
        content: "I'm having trouble connecting right now. Please try again.",
        timestamp: new Date(),
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-slate-50">
      {/* Header */}
      <div className="px-6 py-4 bg-white border-b border-slate-200 flex justify-between items-center shadow-sm z-10">
        <div>
          <h2 className="text-lg font-bold text-slate-800 flex items-center">
            <Sparkles className="w-5 h-5 text-primary-500 mr-2" />
            Memory Assistant
          </h2>
          <p className="text-xs text-slate-500">Powered by Gemini 2.5</p>
        </div>
        <div className="text-xs text-slate-400">
          Session ID: 8f92-ka29
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        {messages.map((msg) => (
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
              <div
                className={`px-5 py-3.5 rounded-2xl text-sm shadow-sm leading-relaxed whitespace-pre-wrap ${
                  msg.role === 'user'
                    ? 'bg-indigo-600 text-white rounded-tr-none'
                    : 'bg-white text-slate-800 border border-slate-100 rounded-tl-none'
                }`}
              >
                {msg.content}
              </div>
              
              {/* Sources (for model only) */}
              {msg.role === 'model' && msg.sources && msg.sources.length > 0 && (
                <div className="mt-3 ml-1">
                   <p className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wider">Relevant Memories</p>
                   <div className="flex space-x-3 overflow-x-auto pb-2 no-scrollbar max-w-full">
                     {msg.sources.map(src => (
                       <div key={src.id} className="flex-shrink-0 w-32 bg-white rounded-lg border border-slate-200 overflow-hidden shadow-sm hover:shadow-md cursor-pointer transition-all">
                         <div className="h-20 w-full overflow-hidden">
                           <img src={src.src} alt="source" className="w-full h-full object-cover" />
                         </div>
                         <div className="p-2 bg-slate-50">
                            <p className="text-[10px] text-slate-500 font-medium truncate">{new Date(src.date).toLocaleDateString()}</p>
                            <p className="text-[10px] text-slate-800 truncate">{src.location}</p>
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
        ))}
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
        <form
          onSubmit={handleSend}
          className="max-w-3xl mx-auto relative flex items-center bg-slate-50 rounded-xl border border-slate-200 focus-within:ring-2 focus-within:ring-primary-100 focus-within:border-primary-400 transition-all"
        >
          <button
            type="button"
            className="p-3 text-slate-400 hover:text-primary-600 transition-colors"
            title="Upload image for analysis"
          >
            <ImageIcon size={20} />
          </button>
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
            disabled={!input.trim() || loading}
            className={`p-2 m-1.5 rounded-lg transition-all ${
              input.trim() && !loading
                ? 'bg-primary-600 text-white hover:bg-primary-700 shadow-sm'
                : 'bg-slate-200 text-slate-400 cursor-not-allowed'
            }`}
          >
            <Send size={18} />
          </button>
        </form>
        <p className="text-center text-[10px] text-slate-400 mt-2">
          Lifelog AI may display inaccurate info. Verify important details.
        </p>
      </div>
    </div>
  );
};
