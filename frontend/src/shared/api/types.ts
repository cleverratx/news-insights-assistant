import type { components } from '@/shared/api/schema';

type Schemas = components['schemas'];

export type CompanyProfile = Schemas['CompanyProfile'];
export type Feed = Schemas['Feed'];
export type FeedItem = Schemas['FeedItem'];
export type RelatedFeed = Schemas['RelatedFeed'];
export type GeneratedInsight = Schemas['GeneratedInsight'];
export type InsightRequest = Schemas['InsightRequest'];
export type LabeledStatement = Schemas['LabeledStatement'];
export type AgentTrace = Schemas['AgentTrace'];
