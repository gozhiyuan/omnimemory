import { MemoryItem, UserStats } from './types';

export const MOCK_STATS: UserStats = {
  totalMemories: 1243,
  storageUsedGB: 4.2,
  thisWeekUploads: 48,
  connectedSources: 2,
};

export const MOCK_MEMORIES: MemoryItem[] = [
  {
    id: '1',
    type: 'image',
    src: 'https://picsum.photos/800/600?random=1',
    caption: 'Hiking in the Swiss Alps, beautiful sunny day.',
    date: '2023-10-15T14:30:00',
    location: 'Zermatt, Switzerland',
    processed: true,
  },
  {
    id: '2',
    type: 'image',
    src: 'https://picsum.photos/800/600?random=2',
    caption: 'Coffee with Sarah at Blue Bottle.',
    date: '2023-10-18T09:15:00',
    location: 'Tokyo, Japan',
    processed: true,
  },
  {
    id: '3',
    type: 'image',
    src: 'https://picsum.photos/800/600?random=3',
    caption: 'Late night coding session, working on the new MVP.',
    date: '2023-11-02T23:45:00',
    location: 'Home Office',
    processed: true,
  },
  {
    id: '4',
    type: 'image',
    src: 'https://picsum.photos/800/600?random=4',
    caption: 'Golden retriever puppy playing in the park.',
    date: '2023-11-05T16:20:00',
    location: 'Central Park, NY',
    processed: true,
  },
  {
    id: '5',
    type: 'image',
    src: 'https://picsum.photos/800/600?random=5',
    caption: 'Delicious sushi dinner for anniversary.',
    date: '2023-11-10T19:30:00',
    location: 'Sushi Nakazawa',
    processed: true,
  },
  {
    id: '6',
    type: 'image',
    src: 'https://picsum.photos/800/600?random=6',
    caption: 'Snowboarding trip with the college crew.',
    date: '2023-12-20T10:00:00',
    location: 'Whistler, Canada',
    processed: true,
  }
];

export const ACTIVITY_DATA = [
  { name: 'Mon', count: 12 },
  { name: 'Tue', count: 19 },
  { name: 'Wed', count: 3 },
  { name: 'Thu', count: 25 },
  { name: 'Fri', count: 42 },
  { name: 'Sat', count: 15 },
  { name: 'Sun', count: 8 },
];
