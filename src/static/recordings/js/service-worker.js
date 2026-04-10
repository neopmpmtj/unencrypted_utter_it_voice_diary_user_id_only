/**
 * Voice Diary Service Worker
 * 
 * Provides offline support and background sync for recordings.
 */

const CACHE_NAME = 'voicediary-v4';
const OFFLINE_RECORDINGS_STORE = 'offline-recordings';

// Files to cache for offline access
const STATIC_ASSETS = [
    '/voice/',
    '/static/recordings/js/audio_recorder.js',
    '/static/css/style.css',
];

/**
 * Install event - cache static assets
 */
self.addEventListener('install', (event) => {
    console.log('[ServiceWorker] Install');
    
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('[ServiceWorker] Caching static assets');
                return cache.addAll(STATIC_ASSETS);
            })
            .catch((error) => {
                console.error('[ServiceWorker] Cache failed:', error);
            })
    );
    
    // Activate immediately
    self.skipWaiting();
});

/**
 * Activate event - clean up old caches
 */
self.addEventListener('activate', (event) => {
    console.log('[ServiceWorker] Activate');
    
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((name) => name !== CACHE_NAME)
                    .map((name) => {
                        console.log('[ServiceWorker] Deleting old cache:', name);
                        return caches.delete(name);
                    })
            );
        })
    );
    
    // Claim clients immediately
    event.waitUntil(clients.claim());
});

/**
 * Fetch event - serve from cache, fall back to network
 */
self.addEventListener('fetch', (event) => {
    // Skip non-GET requests
    if (event.request.method !== 'GET') {
        return;
    }
    
    // Skip API requests (let them fail naturally for offline handling)
    if (event.request.url.includes('/api/') || event.request.url.includes('/voice/upload')) {
        return;
    }
    
    event.respondWith(
        caches.match(event.request)
            .then((response) => {
                if (response) {
                    return response;
                }
                return fetch(event.request);
            })
            .catch(() => {
                // Return offline page if available
                if (event.request.mode === 'navigate') {
                    return caches.match('/voice/');
                }
            })
    );
});

/**
 * Background sync event - sync offline recordings
 */
self.addEventListener('sync', (event) => {
    console.log('[ServiceWorker] Sync event:', event.tag);
    
    if (event.tag === 'sync-recordings') {
        event.waitUntil(syncOfflineRecordings());
    }
});

/**
 * Sync offline recordings to server
 */
async function syncOfflineRecordings() {
    console.log('[ServiceWorker] Syncing offline recordings');
    
    try {
        const db = await openDB();
        const recordings = await getAllRecordings(db);
        
        console.log('[ServiceWorker] Found', recordings.length, 'offline recordings');
        
        for (const recording of recordings) {
            try {
                const formData = new FormData();
                const extension = recording.mimeType.includes('webm') ? 'webm' : 'wav';
                formData.append('audio', recording.blob, `recording.${extension}`);
                formData.append('template_type', recording.templateType || 'plain');
                if (recording.transcribeOnly) {
                    formData.append('transcribe_only', '1');
                }
                const headers = { 'Accept': 'application/json' };
                if (recording.csrfToken) {
                    headers['X-CSRFToken'] = recording.csrfToken;
                }
                const response = await fetch('/voice/upload/', {
                    method: 'POST',
                    body: formData,
                    headers,
                    credentials: 'same-origin',
                });
                
                if (response.ok) {
                    await deleteRecording(db, recording.id);
                    console.log('[ServiceWorker] Synced recording:', recording.id);
                    
                    // Notify clients
                    const clients = await self.clients.matchAll();
                    clients.forEach((client) => {
                        client.postMessage({
                            type: 'recording-synced',
                            id: recording.id,
                        });
                    });
                }
            } catch (error) {
                console.error('[ServiceWorker] Sync failed for recording:', recording.id, error);
            }
        }
    } catch (error) {
        console.error('[ServiceWorker] Sync error:', error);
    }
}

/**
 * Open IndexedDB
 */
function openDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open('VoiceDiaryDB', 1);
        
        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve(request.result);
        
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(OFFLINE_RECORDINGS_STORE)) {
                db.createObjectStore(OFFLINE_RECORDINGS_STORE, { keyPath: 'id', autoIncrement: true });
            }
        };
    });
}

/**
 * Get all offline recordings
 */
function getAllRecordings(db) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(OFFLINE_RECORDINGS_STORE, 'readonly');
        const store = tx.objectStore(OFFLINE_RECORDINGS_STORE);
        const request = store.getAll();
        
        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve(request.result);
    });
}

/**
 * Delete a recording from IndexedDB
 */
function deleteRecording(db, id) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(OFFLINE_RECORDINGS_STORE, 'readwrite');
        const store = tx.objectStore(OFFLINE_RECORDINGS_STORE);
        const request = store.delete(id);
        
        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve();
    });
}
