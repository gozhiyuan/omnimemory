import React, { useState } from 'react';
import { UploadCloud, CheckCircle2, FileImage, X, AlertCircle } from 'lucide-react';

export const UploadManager: React.FC = () => {
  const [dragActive, setDragActive] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [success, setSuccess] = useState(false);

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
  };

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleUpload = () => {
    if (files.length === 0) return;
    setUploading(true);
    // Simulate API upload
    setTimeout(() => {
      setUploading(false);
      setSuccess(true);
      setFiles([]);
    }, 2000);
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
                  onClick={() => setFiles([])}
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
                    Processing...
                  </>
                ) : 'Start Processing'}
              </button>
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
                <p className="text-xs text-green-600 flex items-center">
                  <CheckCircle2 size={12} className="mr-1" />
                  Synced (2 hrs ago)
                </p>
              </div>
            </div>
            <button className="text-xs border border-slate-200 px-3 py-1.5 rounded-md hover:bg-slate-50">
              Manage
            </button>
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
