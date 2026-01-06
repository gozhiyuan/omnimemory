import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { useSettings } from '../contexts/SettingsContext';

type PageMotionProps = {
  children: React.ReactNode;
  className?: string;
};

export const PageMotion: React.FC<PageMotionProps> = ({ children, className }) => {
  const prefersReduced = useReducedMotion();
  const { settings } = useSettings();
  const reduceMotion = settings.appearance.reduceMotion || prefersReduced;

  return (
    <motion.div
      className={className}
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduceMotion ? { duration: 0 } : { duration: 0.3, ease: 'easeOut' }}
    >
      {children}
    </motion.div>
  );
};
