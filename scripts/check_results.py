from sqlalchemy import create_engine, text

engine = create_engine('postgresql://postgres:postgres@localhost:5432/glad_labs_dev')

print('\n' + '='*80)
print('📝 RECENT BLOG POSTS (Content Length Check)')
print('='*80)

with engine.connect() as conn:
    result = conn.execute(text('''
        SELECT 
            id, 
            title, 
            length(content) as content_length,
            created_at
        FROM posts
        ORDER BY created_at DESC
        LIMIT 5
    '''))
    
    posts = result.fetchall()
    if posts:
        for post_id, title, length, created in posts:
            quality = "✅ QUALITY" if length > 5000 else "⚠️ FALLBACK" if length < 5000 else "✨ EXCELLENT"
            print(f'\n{quality} {title[:60]}...')
            print(f'   Length: {length:,} characters | Created: {created}')
    else:
        print('\n❌ No blog posts found in database')

print('\n' + '='*80)
print('📊 WORKFLOW EXECUTIONS WITH MODEL TRACKING')
print('='*80)

with engine.connect() as conn:
    result = conn.execute(text('''
        SELECT 
            id, 
            selected_model, 
            execution_mode, 
            execution_status,
            created_at
        FROM workflow_executions
        ORDER BY created_at DESC
        LIMIT 5
    '''))
    
    executions = result.fetchall()
    if executions:
        for exec_id, model, mode, status, created in executions:
            print(f'\nExecution {exec_id}')
            print(f'  Model: {model or "None"} | Mode: {mode or "agent"} | Status: {status}')
            print(f'  Created: {created}')
    else:
        print('\n❌ No workflow executions found')

print('\n' + '='*80)
