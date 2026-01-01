import { GoogleGenAI } from "@google/genai";
import { MemoryItem } from "../types";

// In a real app, this would be initialized securely.
// For this demo, we assume the environment variable is set or we handle the missing key gracefully.
const apiKey = process.env.API_KEY || '';

const ai = new GoogleGenAI({ apiKey });

/**
 * Simulates a RAG (Retrieval Augmented Generation) approach by passing relevant memories
 * into the context window of the model.
 */
export const sendMessageToGemini = async (
  message: string,
  contextMemories: MemoryItem[]
): Promise<string> => {
  if (!apiKey) {
    return "API Key is missing. Please set the API_KEY environment variable to use the chat feature.";
  }

  try {
    // 1. Construct the system prompt with "retrieved" context
    const contextString = contextMemories.map(m => 
      `- Date: ${new Date(m.date).toLocaleDateString()}, Location: ${m.location}, Description: ${m.caption}`
    ).join('\n');

    const systemInstruction = `You are OmniMemory, a helpful and warm personal memory assistant. 
    You have access to the user's memories (photos, logs, events) provided in the context below.
    Answer the user's questions based primarily on this context. 
    If the answer isn't in the context, say you don't recall that specific memory but allow general conversation.
    Keep responses concise and friendly.
    
    [User's Memories Context]
    ${contextString}
    `;

    // 2. Call the model
    const response = await ai.models.generateContent({
      model: 'gemini-3-flash-preview',
      contents: message,
      config: {
        systemInstruction: systemInstruction,
      }
    });

    return response.text || "I couldn't generate a response.";
  } catch (error) {
    console.error("Error communicating with Gemini:", error);
    return "Sorry, I encountered an error while processing your request. Please try again later.";
  }
};
