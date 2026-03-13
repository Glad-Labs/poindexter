-- Migration: Fix quality score column types from DECIMAL(3,1) to DECIMAL(5,1)
-- Date: March 2026
-- Issue: #549
-- Purpose: quality_evaluations and related tables used DECIMAL(3,1) which has a max
--          of 99.9, but all scores are on a 0-100 scale (not 0-10 as the migration
--          comment incorrectly stated). DECIMAL(5,1) accommodates up to 999.9, which
--          correctly handles 0-100 scores including the edge value 100.0.
--          Also corrects the column and table comments that stated a 0-10 scale.

-- Fix quality_evaluations: all criterion scores are 0-100
ALTER TABLE quality_evaluations
    ALTER COLUMN overall_score TYPE DECIMAL(5,1),
    ALTER COLUMN clarity       TYPE DECIMAL(5,1),
    ALTER COLUMN accuracy      TYPE DECIMAL(5,1),
    ALTER COLUMN completeness  TYPE DECIMAL(5,1),
    ALTER COLUMN relevance     TYPE DECIMAL(5,1),
    ALTER COLUMN seo_quality   TYPE DECIMAL(5,1),
    ALTER COLUMN readability   TYPE DECIMAL(5,1),
    ALTER COLUMN engagement    TYPE DECIMAL(5,1);

-- Fix quality_improvement_logs
ALTER TABLE quality_improvement_logs
    ALTER COLUMN initial_score          TYPE DECIMAL(5,1),
    ALTER COLUMN improved_score         TYPE DECIMAL(5,1),
    ALTER COLUMN score_improvement      TYPE DECIMAL(5,1),
    ALTER COLUMN best_improvement_points TYPE DECIMAL(5,1);

-- Fix quality_metrics_daily
ALTER TABLE quality_metrics_daily
    ALTER COLUMN average_score              TYPE DECIMAL(5,1),
    ALTER COLUMN avg_clarity                TYPE DECIMAL(5,1),
    ALTER COLUMN avg_accuracy               TYPE DECIMAL(5,1),
    ALTER COLUMN avg_completeness           TYPE DECIMAL(5,1),
    ALTER COLUMN avg_relevance              TYPE DECIMAL(5,1),
    ALTER COLUMN avg_seo_quality            TYPE DECIMAL(5,1),
    ALTER COLUMN avg_readability            TYPE DECIMAL(5,1),
    ALTER COLUMN avg_engagement             TYPE DECIMAL(5,1),
    ALTER COLUMN avg_refinements_per_content TYPE DECIMAL(5,1);

-- Fix total_improvement_points (DECIMAL(5,1) for aggregate totals)
ALTER TABLE quality_metrics_daily
    ALTER COLUMN total_improvement_points TYPE DECIMAL(7,1);

-- Update column comments to reflect correct 0-100 scale
COMMENT ON COLUMN quality_evaluations.overall_score IS 'Average of 7 criteria (0-100 scale), passing threshold is 70.0 (equivalent to 7.0/10)';
COMMENT ON COLUMN quality_evaluations.clarity       IS 'Clarity criterion score (0-100 scale)';
COMMENT ON COLUMN quality_evaluations.accuracy      IS 'Accuracy criterion score (0-100 scale)';
COMMENT ON COLUMN quality_evaluations.completeness  IS 'Completeness criterion score (0-100 scale)';
COMMENT ON COLUMN quality_evaluations.relevance     IS 'Relevance criterion score (0-100 scale)';
COMMENT ON COLUMN quality_evaluations.seo_quality   IS 'SEO quality criterion score (0-100 scale)';
COMMENT ON COLUMN quality_evaluations.readability   IS 'Readability criterion score (0-100 scale)';
COMMENT ON COLUMN quality_evaluations.engagement    IS 'Engagement criterion score (0-100 scale)';
COMMENT ON COLUMN quality_evaluations.passing       IS 'True if overall_score >= 70.0 (70/100 = passing grade), indicates content is ready for publishing';

-- Verify migration
SELECT 'Quality score column types fixed to DECIMAL(5,1) for 0-100 scale' AS status;
