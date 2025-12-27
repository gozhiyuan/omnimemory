import React, { useEffect, useState } from 'react';
import { UploadCloud, CheckCircle2, FileImage, X, AlertCircle } from 'lucide-react';
import { apiGet, apiPost } from '../services/api';
import {
  GooglePhotosAuthUrlResponse,
  GooglePhotosPickerSessionResponse,
  GooglePhotosStatus,
  GooglePhotosSyncRequest,
  GooglePhotosSyncResponse,
  IngestResponse,
  GooglePhotosPickerItem,
  GooglePhotosPickerItemsResponse,
  TimelineDay,
  TimelineItem,
  UploadUrlResponse,
} from '../types';

export const UploadManager: React.FC = () => {
  const [dragActive, setDragActive] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadedCount, setUploadedCount] = useState(0);
  const [googleStatus, setGoogleStatus] = useState<GooglePhotosStatus | null>(null);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [googleError, setGoogleError] = useState<string | null>(null);
  const [pickerLoading, setPickerLoading] = useState(false);
  const [pickerError, setPickerError] = useState<string | null>(null);
  const [pickerSessionId, setPickerSessionId] = useState<string | null>(null);
  const [syncLoading, setSyncLoading] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [recentItems, setRecentItems] = useState<TimelineItem[]>([]);
  const [recentError, setRecentError] = useState<string | null>(null);
  const [selectedItems, setSelectedItems] = useState<GooglePhotosPickerItem[]>([]);
  const [selectedLoading, setSelectedLoading] = useState(false);
  const [selectedError, setSelectedError] = useState<string | null>(null);

  const inferItemType = (file: File) => {
    if (file.type.startsWith('image/')) return 'photo';
    if (file.type.startsWith('video/')) return 'video';
    if (file.type.startsWith('audio/')) return 'audio';
    return 'document';
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFiles(e.dataTransfer.files);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    if (e.target.files && e.target.files.length > 0) {
      handleFiles(e.target.files);
    }
  };

  const handleFiles = (fileList: FileList) => {
    const newFiles = Array.from(fileList);
    setFiles(prev => [...prev, ...newFiles]);
    setSuccess(false);
    setError(null);
    setUploadedCount(0);
  };

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
    setSuccess(false);
  };

  const clearFiles = () => {
    setFiles([]);
    setSuccess(false);
    setError(null);
    setUploadedCount(0);
  };

  const handleUpload = async () => {
    if (files.length === 0) return;
    setUploading(true);
    setError(null);
    setSuccess(false);
    setUploadedCount(0);

    try {
      for (const file of files) {
        const contentType = file.type || 'application/octet-stream';
        const uploadMeta = await apiPost<UploadUrlResponse>('/storage/upload-url', {
          filename: file.name,
          content_type: contentType,
          prefix: 'uploads/ui',
        });
        if (!uploadMeta.url) {
          throw new Error(`Upload URL missing for ${file.name}`);
        }

        const headers = { ...(uploadMeta.headers || {}), 'Content-Type': contentType };
        const uploadResponse = await fetch(uploadMeta.url, {
          method: 'PUT',
          headers,
          body: file,
        });
        if (!uploadResponse.ok) {
          const responseText = await uploadResponse.text();
          throw new Error(
            `Upload failed for ${file.name}: ${uploadResponse.status} ${responseText || ''}`.trim()
          );
        }

        await apiPost<IngestResponse>('/upload/ingest', {
          storage_key: uploadMeta.key,
          item_type: inferItemType(file),
          content_type: contentType,
          original_filename: file.name,
        });

        setUploadedCount((count) => count + 1);
      }

      setSuccess(true);
      setFiles([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setUploading(false);
    }
  };

  const loadGoogleStatus = async () => {
    setGoogleLoading(true);
    setGoogleError(null);
    try {
      const status = await apiGet<GooglePhotosStatus>('/integrations/google/photos/status');
      setGoogleStatus(status);
    } catch (err) {
      setGoogleError(err instanceof Error ? err.message : 'Failed to load Google Photos status.');
    } finally {
      setGoogleLoading(false);
    }
  };

  const loadRecentItems = async () => {
    setRecentError(null);
    try {
      const days = await apiGet<TimelineDay[]>('/timeline?limit=12&provider=google_photos');
      const flattened = days.flatMap((day) => day.items);
      setRecentItems(flattened.slice(0, 8));
    } catch (err) {
      setRecentError(err instanceof Error ? err.message : 'Failed to load recent items.');
    }
  };

  const handleGoogleConnect = async () => {
    setGoogleLoading(true);
    setGoogleError(null);
    try {
      const response = await apiGet<GooglePhotosAuthUrlResponse>('/integrations/google/photos/auth-url');
      window.location.href = response.auth_url;
    } catch (err) {
      setGoogleError(err instanceof Error ? err.message : 'Failed to start Google Photos connection.');
      setGoogleLoading(false);
    }
  };

  const handleGooglePicker = async () => {
    setPickerLoading(true);
    setPickerError(null);
    setSyncMessage(null);
    setSelectedItems([]);
    setSelectedError(null);
    try {
      const response = await apiPost<GooglePhotosPickerSessionResponse>('/integrations/google/photos/picker-session');
      setPickerSessionId(response.session_id);
      window.open(response.picker_uri, '_blank', 'noopener,noreferrer');
    } catch (err) {
      setPickerError(err instanceof Error ? err.message : 'Failed to open Google Photos picker.');
    } finally {
      setPickerLoading(false);
    }
  };

  const loadPickerSelection = async () => {
    if (!pickerSessionId) {
      setSelectedError('Start a picker session before loading selections.');
      return;
    }
    setSelectedLoading(true);
    setSelectedError(null);
    try {
      const response = await apiGet<GooglePhotosPickerItemsResponse>(
        `/integrations/google/photos/picker-items?session_id=${encodeURIComponent(pickerSessionId)}`
      );
      setSelectedItems(response.items);
    } catch (err) {
      setSelectedError(err instanceof Error ? err.message : 'Failed to load picker selections.');
    } finally {
      setSelectedLoading(false);
    }
  };

  const handleGoogleSync = async () => {
    if (!pickerSessionId) {
      setPickerError('Start a picker session before syncing.');
      return;
    }
    setSyncLoading(true);
    setSyncMessage(null);
    setPickerError(null);
    try {
      const payload: GooglePhotosSyncRequest = { session_id: pickerSessionId };
      const response = await apiPost<GooglePhotosSyncResponse>('/integrations/google/photos/sync', payload);
      setSyncMessage(`Sync queued (task ${response.task_id}).`);
      void loadRecentItems();
    } catch (err) {
      setPickerError(err instanceof Error ? err.message : 'Failed to queue Google Photos sync.');
    } finally {
      setSyncLoading(false);
    }
  };

  useEffect(() => {
    void loadGoogleStatus();
    void loadRecentItems();
  }, []);

  const formatGoogleStatus = () => {
    if (!googleStatus?.connected) {
      return 'Not connected';
    }
    if (googleStatus.connected_at) {
      const connectedAt = new Date(googleStatus.connected_at);
      return `Connected ${connectedAt.toLocaleString()}`;
    }
    return 'Connected';
  };

  return (
    <div className="p-8 max-w-4xl mx-auto">
       <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900">Ingestion Pipeline</h1>
        <p className="text-slate-500 mt-1">Upload photos, videos, or connect external accounts.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        {/* Manual Upload Area */}
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-slate-800">Manual Upload</h2>
          <div 
            className={`relative border-2 border-dashed rounded-xl p-8 flex flex-col items-center justify-center text-center transition-colors min-h-[300px] ${
              dragActive ? 'border-primary-500 bg-primary-50' : 'border-slate-300 bg-slate-50'
            }`}
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
          >
            <input 
              type="file" 
              multiple 
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
              onChange={handleChange}
              accept="image/*,video/*"
            />
            <div className="p-4 bg-white rounded-full shadow-sm mb-4 pointer-events-none">
              <UploadCloud className={`w-8 h-8 ${dragActive ? 'text-primary-600' : 'text-slate-400'}`} />
            </div>
            <p className="text-sm font-medium text-slate-900 pointer-events-none">
              Click to upload or drag and drop
            </p>
            <p className="text-xs text-slate-500 mt-2 max-w-xs pointer-events-none">
              Supported: JPG, PNG, MP4, MOV (Max 5GB per batch)
            </p>
          </div>

          {files.length > 0 && (
            <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-sm animate-fade-in">
              <div className="flex justify-between items-center mb-3">
                <span className="text-sm font-medium text-slate-700">{files.length} files selected</span>
                <button 
                  onClick={clearFiles}
                  className="text-xs text-red-500 hover:text-red-600"
                >
                  Clear all
                </button>
              </div>
              <div className="max-h-40 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
                {files.map((file, idx) => (
                  <div key={idx} className="flex items-center justify-between text-xs p-2 bg-slate-50 rounded">
                    <div className="flex items-center truncate">
                      <FileImage className="w-4 h-4 text-slate-400 mr-2" />
                      <span className="truncate max-w-[150px]">{file.name}</span>
                    </div>
                    <button onClick={() => removeFile(idx)} className="text-slate-400 hover:text-slate-600">
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
              <button 
                onClick={handleUpload}
                disabled={uploading}
                className="w-full mt-4 bg-primary-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-primary-700 transition-colors disabled:opacity-50 flex items-center justify-center"
              >
                {uploading ? (
                  <>
                    <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    Uploading {uploadedCount}/{files.length}
                  </>
                ) : 'Start Processing'}
              </button>
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-center">
              <AlertCircle className="w-5 h-5 mr-2" />
              <span className="text-sm">{error}</span>
            </div>
          )}

          {success && (
             <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-lg flex items-center">
               <CheckCircle2 className="w-5 h-5 mr-2" />
               <span className="text-sm">Batch uploaded & queued for processing!</span>
             </div>
          )}
        </div>

        {/* Integration Cards */}
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-slate-800">Connected Sources</h2>
          
          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
                 <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/c/c1/Google_%22G%22_logo.svg/768px-Google_%22G%22_logo.svg.png" className="w-5 h-5" alt="Google" />
              </div>
              <div>
                <h3 className="text-sm font-medium text-slate-900">Google Photos</h3>
                <p className={`text-xs flex items-center ${googleStatus?.connected ? 'text-green-600' : 'text-slate-500'}`}>
                  {googleStatus?.connected ? (
                    <CheckCircle2 size={12} className="mr-1" />
                  ) : (
                    <AlertCircle size={12} className="mr-1" />
                  )}
                  {googleLoading ? 'Checking status...' : formatGoogleStatus()}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                className="text-xs border border-slate-200 px-3 py-1.5 rounded-md hover:bg-slate-50 disabled:opacity-50"
                onClick={handleGoogleConnect}
                disabled={googleLoading}
              >
                {googleStatus?.connected ? 'Reconnect' : 'Connect'}
              </button>
              <button
                className="text-xs bg-primary-600 text-white px-3 py-1.5 rounded-md hover:bg-primary-700 disabled:opacity-50"
                onClick={handleGooglePicker}
                disabled={!googleStatus?.connected || pickerLoading}
              >
                {pickerLoading ? 'Opening...' : 'Select photos'}
              </button>
            </div>
          </div>
          {googleError && (
            <div className="text-xs text-red-600">{googleError}</div>
          )}
          {pickerError && (
            <div className="text-xs text-red-600">{pickerError}</div>
          )}
          {googleStatus?.connected && (
            <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 text-xs text-slate-600 space-y-2">
              <p>Select photos in the Google Picker, then start ingestion below.</p>
              <p>Already ingested items are skipped automatically during sync.</p>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  className="text-xs border border-slate-300 px-3 py-1.5 rounded-md hover:bg-white disabled:opacity-50"
                  onClick={loadPickerSelection}
                  disabled={selectedLoading || !pickerSessionId}
                >
                  {selectedLoading ? 'Loading selection...' : 'Load selection'}
                </button>
                {selectedItems.length > 0 && (
                  <span className="text-slate-500">Selected {selectedItems.length} photos</span>
                )}
              </div>
              {selectedError && <p className="text-xs text-red-600">{selectedError}</p>}
              {selectedItems.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {selectedItems.slice(0, 6).map((item) =>
                    item.base_url ? (
                      <img
                        key={item.id}
                        src={item.base_url}
                        alt={item.filename || 'Selected Google photo'}
                        className="w-12 h-12 rounded-md object-cover border border-slate-200"
                      />
                    ) : null
                  )}
                </div>
              )}
              <button
                className="text-xs bg-slate-900 text-white px-3 py-1.5 rounded-md hover:bg-slate-800 disabled:opacity-50"
                onClick={handleGoogleSync}
                disabled={syncLoading || !pickerSessionId}
              >
                {syncLoading ? 'Queueing sync...' : 'Ingest selected photos'}
              </button>
              {syncMessage && <p className="text-green-600">{syncMessage}</p>}
              {!pickerSessionId && <p className="text-slate-500">Waiting for picker selection.</p>}
            </div>
          )}
          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-medium text-slate-900">Recently ingested</h3>
              <button
                className="text-xs text-slate-500 hover:text-slate-700"
                onClick={loadRecentItems}
                type="button"
              >
                Refresh
              </button>
            </div>
            {recentError && <p className="text-xs text-red-600">{recentError}</p>}
            {recentItems.length === 0 && !recentError ? (
              <p className="text-xs text-slate-500">No recent items yet.</p>
            ) : (
              <ul className="space-y-2 text-xs text-slate-600">
                {recentItems.map((item) => (
                  <li key={item.id} className="flex items-start gap-2">
                    <CheckCircle2 className="w-4 h-4 text-green-600 mt-0.5" />
                    {item.item_type === 'photo' && item.download_url ? (
                      <img
                        src={item.download_url}
                        alt={item.original_filename || 'Google Photos thumbnail'}
                        className="w-12 h-12 rounded-md object-cover border border-slate-200"
                      />
                    ) : null}
                    <div>
                      <p className="text-slate-800 font-medium">
                        {item.original_filename || item.storage_key}
                      </p>
                      <p className="text-slate-500">
                        {item.captured_at ? new Date(item.captured_at).toLocaleString() : 'Unknown date'}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="w-10 h-10 bg-slate-50 rounded-lg flex items-center justify-center text-slate-800">
                <svg className="w-6 h-6" viewBox="0 0 24 24" fill="currentColor"><path d="M17.05 20.28c-.98.95-2.05.88-3.08.4-.55-.26-1.1-.5-1.68-.5-.59 0-1.18.26-1.74.52-1 .47-2.04.53-3.03-.43-1.6-1.57-2.8-4.44-1.16-7.29 1.1-1.91 3.03-2.14 4.09-2.16 1.05-.03 2 .69 2.65.69.64 0 1.83-.87 3.08-.73 1.3.06 2.3.52 3 1.54-2.6 1.56-2.17 4.75.47 5.92-.58 1.48-1.4 2.94-2.6 4.04zm-4.14-15.65c1.1-1.34 1.85-3.2 1.66-4.63-1.6.06-3.52 1.07-4.66 2.43-.97 1.14-1.81 2.97-1.59 4.7 1.78.14 3.6-1.15 4.59-2.5z"/></svg>
              </div>
              <div>
                <h3 className="text-sm font-medium text-slate-900">iCloud Photos</h3>
                <p className="text-xs text-slate-500 flex items-center">
                  <AlertCircle size={12} className="mr-1" />
                  Action required
                </p>
              </div>
            </div>
            <button className="text-xs bg-slate-900 text-white px-3 py-1.5 rounded-md hover:bg-slate-800">
              Connect
            </button>
          </div>

          <div className="bg-slate-50 p-4 rounded-xl border border-dashed border-slate-300 flex items-center justify-center text-slate-400 cursor-pointer hover:bg-slate-100 hover:text-slate-500 transition-colors">
            <span className="text-xs font-medium">+ Add new source</span>
          </div>

        </div>
      </div>
    </div>
  );
};
