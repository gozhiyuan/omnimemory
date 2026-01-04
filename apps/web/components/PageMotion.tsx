import React from 'react';
import { motion } from 'framer-motion';

type PageMotionProps = {
  children: React.ReactNode;
  className?: string;
};

export const PageMotion: React.FC<PageMotionProps> = ({ children, className }) => (
  <motion.div
    className={className}
    initial={{ opacity: 0, y: 8 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.3, ease: 'easeOut' }}
  >
    {children}
  </motion.div>
);
